import os
import logging
import subprocess
import time
import json
import textwrap
import shutil
import cfnresponse
import urllib3
import boto3
from botocore.exceptions import ClientError

# define list of supported resource properties
SUPPORTED_RESOURCE_PROPERTIES = ['ServiceToken', 'Backend', 'Environment', 'Variables', 'Configuration', 'ExecutionLogsTargetArn']

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize CloudWatch Logs client
cloudwatch_logs = boto3.client('logs')

def send_response(event, context, response_status, response_reason=None, response_data={}):
    """
    Send a response to CloudFormation about the result of a custom resource
    invocation.

    :param event: The event that triggered the Lambda function invocation.
    :param context: The context object that describes the Lambda function
                    invocation.
    :param response_status: The status of the response. Must be one of
        cfnresponse.SUCCESS, cfnresponse.FAILED, or cfnresponse.SUCCESS_WITH_NO_OP.
    :param response_reason: An optional string that describes the reason for
        the response status.
    :param response_data: An optional dictionary of key-value pairs that will
        be stored in the custom resource's attributes.
    """
    response_url = event['ResponseURL']

    physical_resource_id = event['LogicalResourceId'] # assume that replacement will be managed by terraform, see https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/crpg-ref-responses.html#crpg-ref-responses-physicalresourceid.title

    try:
        cfnresponse.send(event, context, responseStatus=response_status, responseData=response_data, physicalResourceId=physical_resource_id, reason=response_reason)
    except Exception as e:
        logger.error(f"Failed to send response: {e}")

def get_terraform_configuration_content(terraform_configuration):

    """
    Fetch the Terraform configuration content from the given URL.

    If the URL is an HTTP(S) endpoint, fetch the content from the URL.
    If the URL is an S3 endpoint, fetch the content from the S3 bucket.
    If the URL is not a valid HTTP(S) or S3 endpoint, assume it is the inline text of the Terraform configuration.

    Returns:
        str: The content of the Terraform configuration.
    """
    try:
        parsed_url = urllib3.util.parse_url(terraform_configuration)

        if parsed_url.scheme in ('http', 'https'):
            # Get contents from http endpoint
            try:
                response = urllib3.request('GET', terraform_configuration, redirect=True, retries=10, timeout=5)
            except Exception as e:
                logger.warning(f"Failed to get Terraform configuration from URL {terraform_configuration} - {e}")
                raise Exception(f"Failed to get Terraform configuration from URL {terraform_configuration} - make sure it is reachable by the Custom Resource service.")
            if not 200 <= response.status < 300:
                raise Exception(f"Failed to get Terraform configuration from URL {terraform_configuration} - received non-2xx status {response.status}")
            terraform_configuration_content = response.data.decode('utf-8')

        elif parsed_url.scheme == 's3':
            # Get contents from S3 endpoint
            try:
                s3 = boto3.client('s3')
                response = s3.get_object(Bucket=parsed_url.host, Key=parsed_url.path.lstrip('/'))
                terraform_configuration_content = response['Body'].read().decode('utf-8')
            except Exception as e:
                logger.warning(f"Failed to get Terraform configuration from S3 {terraform_configuration} - {e}")
                raise Exception(f"Failed to get Terraform configuration from S3 {terraform_configuration} - make sure it is reachable by the Custom Resource service.")

        else:
            # Treat it as inline text
            terraform_configuration_content = terraform_configuration

    except urllib3.exceptions.LocationValueError as e:
        # assume it's a non-parseable URL, treat it as inline text
        terraform_configuration_content = terraform_configuration

    return terraform_configuration_content


def setup_environment_variables(resource_properties):

    """
    Set up the environment variables for the Terraform execution.

    This function takes the custom resource properties as an argument and
    returns a dictionary of environment variables that should be set when
    executing Terraform.

    The properties are validated to ensure that they are a dictionary of
    scalars. The 'TF_IN_AUTOMATION' and 'TF_INPUT' variables are always set to
    'true' and 'false' respectively.

    :param resource_properties: A dictionary of custom resource properties.
    :return: A dictionary of environment variables.
    """

    environment = resource_properties.get('Environment', {})

    # check that environment is a dictionary
    if not isinstance(environment, dict):
        raise Exception("Environment property, if set, must be a map of scalars.")
    # check that all environment variables are scalars
    for key, value in environment.items():
        if not isinstance(value, (str, int, float, complex, bool)):
            raise Exception("Environment property, if set, must be a map of scalars.")

    if 'TF_IN_AUTOMATION' in environment:
        raise Exception("Setting environment variable 'TF_IN_AUTOMATION' is not supported, it is always set to 'true'")
    environment['TF_IN_AUTOMATION'] = 'true'

    if 'TF_INPUT' in environment:
        raise Exception("Setting environment variable 'TF_INPUT' is not supported, it is always set to 'false'")
    environment['TF_INPUT'] = 'false'

    # Merge with the existing environment variables
    return {**os.environ, **environment}


def prepare_terraform_configuration(resource_properties):
    """
    Prepare and validate the Terraform configuration.

    This function takes the custom resource properties as an argument and
    returns the path to a temporary file containing the Terraform configuration.

    The properties are validated to ensure that they contain the 'Configuration'
    property, which must be a string or a URL. If the property is a URL, the
    content of the URL is fetched and written to the temporary file.

    If the properties contain the 'Variables' property, it must be a dictionary.
    The dictionary is written to a temporary file at /tmp/terraform.tfvars.

    :param resource_properties: A dictionary of custom resource properties.
    :return: The path to a temporary file containing the Terraform configuration.
    """
    terraform_configuration = resource_properties.get('Configuration')
    if not terraform_configuration:
        raise Exception("Terraform configuration not provided")

    terraform_configuration_content = get_terraform_configuration_content(terraform_configuration)

    # Write configuration to /tmp
    config_path = '/tmp/terraform.tf'
    with open(config_path, 'w') as f:
        f.write(terraform_configuration_content)

    # Handle variables, if any
    terraform_variables = resource_properties.get('Variables', {})

    # check that variables is a dictionary
    if not isinstance(terraform_variables, dict):
        raise Exception("Variables property, if set, must be a map.")

    if terraform_variables:
        vars_path = '/tmp/terraform.tfvars'
        with open(vars_path, 'w') as f:
            for key, value in terraform_variables.items():
                if isinstance(value, list):
                    f.write(f'{key} = {json.dumps(value)}\n')
                elif isinstance(value, dict):
                    f.write(f'{key} = {{\n')
                    for subkey, subvalue in value.items():
                        f.write(f'  {subkey} = "{subvalue}"\n')
                    f.write('}\n')
                else:
                    f.write(f'{key} = "{value}"\n')

    return config_path


def clean_terraform_cache():

    """
    Clean up the Terraform cache directory.

    This function is called when the Lambda function is finished executing. It removes
    the cached .terraform directory from /tmp, which is created by Terraform when it
    is run.
    """

    terraform_dir = '/tmp/.terraform'
    if os.path.exists(terraform_dir):
        logger.info("Removing cached .terraform directory")
        shutil.rmtree(terraform_dir)

def log_output(stdout_message, stderr_message, log_group=None, log_stream_name=None):

    """
    Logs Terraform command output (stdout and stderr) to CloudWatch if log group and stream are provided,
    otherwise logs to the console.

    Args:
        stdout_message (str): Standard output from the command.
        stderr_message (str): Standard error from the command.
        log_group (str): CloudWatch log group name for logging Terraform output (optional).
        log_stream_name (str): CloudWatch log stream name for logging Terraform output (optional).
    """

    if log_group and log_stream_name:
        # Log only Terraform command outputs to CloudWatch
        log_to_cloudwatch(stdout_message, stderr_message, log_group, log_stream_name)
    else:
        # Log both stdout and stderr to the console
        if stdout_message:
            logger.info(stdout_message)
        if stderr_message:
            logger.error(stderr_message)

def log_to_cloudwatch(stdout_message, stderr_message, log_group, log_stream_name):

    """
    Sends Terraform command output (stdout and stderr) to CloudWatch Logs.

    This function logs command outputs to a specified CloudWatch log group and stream.
    It creates log events with timestamps and messages for both stdout and stderr,
    and attempts to push them to CloudWatch Logs.

    Args:
        stdout_message (str): Standard output from the command.
        stderr_message (str): Standard error from the command.
        log_group (str): The name of the CloudWatch log group.
        log_stream_name (str): The name of the CloudWatch log stream.

    Raises:
        ClientError: If there's an error while sending logs to CloudWatch.
    """
    log_events = []
    timestamp = int(time.time() * 1000)

    if stdout_message:
        log_events.append({
            'timestamp': timestamp,
            'message': stdout_message
        })

    if stderr_message:
        log_events.append({
            'timestamp': timestamp,
            'message': stderr_message
        })

    if log_events:
        try:
            response = cloudwatch_logs.put_log_events(
                logGroupName=log_group,
                logStreamName=log_stream_name,
                logEvents=log_events
            )
            logger.info(f"Terraform command output successfully sent to CloudWatch Logs: {response}")
        except ClientError as e:
            logger.error(f"Failed to send logs to CloudWatch Logs: {e}")

def run_terraform_command(command, log_group=None, log_stream_name=None, environment=None):

    """
    Runs a Terraform command and logs output to CloudWatch if log group and stream are provided,
    otherwise logs to the console. Raises a RuntimeError if the command fails.

    Args:
        command (list): Terraform command to run (as a list of strings).
        log_group (str): CloudWatch log group name for logging Terraform output (optional).
        log_stream_name (str): CloudWatch log stream name for logging Terraform output (optional).
        environment (dict): Environment variables for the command (optional).

    Returns:
        result: The result of the Terraform command (stdout and stderr).

    Raises:
        RuntimeError: For Terraform errors or subprocess failures.
    """
    try:
        # Run the terraform command and capture the result
        logger.info(f"Running Terraform command: {' '.join(command)}")
        result = subprocess.run(command, cwd='/tmp', env=environment, capture_output=True, text=True)

        # Log Terraform outputs (stdout/stderr) based on log group availability
        log_output(result.stdout, result.stderr, log_group, log_stream_name)

        # Check for terraform-specific error (return code 1)
        if result.returncode == 1:
            raise RuntimeError(f"Terraform error: {result.stderr}")

        # Check for other non-zero return codes
        if result.returncode != 0:
            error_message = (
                f"Terraform command '{' '.join(command)}' failed with return code {result.returncode}.\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}\n"
            )
            logger.error(error_message)

            # Raise a RuntimeError with the detailed message
            raise RuntimeError(error_message)

        return result

    except subprocess.SubprocessError as e:
        # Catch subprocess errors, log them to console, and re-raise as RuntimeError
        logger.error(f"Subprocess error: {str(e)}")
        raise RuntimeError(f"Subprocess error occurred: {str(e)}") from e

    except Exception as e:
        # General exception handler for unexpected issues, log to console
        logger.error(f"Unexpected error: {str(e)}")
        raise RuntimeError(f"Unexpected error: {str(e)}") from e

def get_backend_auto_config_s3_key(event):
    # Get stack name, guid and logical resource ID to generate discriminating key
    """
    Generates an S3 key for storing Terraform state files.

    The key is constructed using the stack name, a unique GUID, and the logical resource ID
    derived from the CloudFormation event.

    Args:
        event (dict): The event data containing 'StackId' and 'LogicalResourceId' keys.

    Returns:
        str: A formatted S3 key for Terraform state storage.
    """
    stack_name = event['StackId'].split('/')[-2]
    guid = event['StackId'].split('/')[-1]
    logical_resource_id = event['LogicalResourceId']

    key = f"terraform-state/{stack_name}/{guid}/{logical_resource_id}"

    return key

def get_backend_auto_config_s3_content(event):
    """
    Generates the Terraform backend configuration content based on environment variables and event data.

    The content is generated using the 'bucket', 'dynamodb_table', and 'key' values.
    The 'key' is generated using the stack name, a unique GUID, and the logical resource ID
    derived from the CloudFormation event.

    Args:
        event (dict): The event data containing 'StackId' and 'LogicalResourceId' keys.

    Returns:
        str: The auto-generated Terraform backend configuration content.
    """
    bucket = os.environ.get("TERRAFORM_BACKEND_S3_BUCKET")
    dynamodb_table = os.environ.get("TERRAFORM_BACKEND_S3_DYNAMODB_TABLE")

    if not bucket or not dynamodb_table:
        raise Exception("Environment variables TERRAFORM_BACKEND_S3_BUCKET and TERRAFORM_BACKEND_S3_DYNAMODB_TABLE must be set.")

    key = get_backend_auto_config_s3_key(event)

    # Auto-generated backend-config values
    backend_config_content = textwrap.dedent(f"""
        bucket         = "{bucket}"
        dynamodb_table = "{dynamodb_table}"
        key            = "{key}"
        encrypt        = "true"
        """)

    return backend_config_content

def build_terraform_init_cmd(terraform_binary, backend_config, event):

    """
    Builds the Terraform init command, adding -backend-config if necessary.

    Args:
        terraform_binary (str): The path to the Terraform binary.
        backend_config (str): The backend configuration. If set to 'Auto', the backend configuration
            is generated using environment variables and the CloudFormation event data.
        event (dict): The CloudFormation event data.

    Returns:
        list: The Terraform init command as a list of arguments.
    """
    init_cmd = [terraform_binary, "init", "-no-color"]

    if backend_config:
        if not isinstance(backend_config, str):
            raise Exception("Backend property, if set, must be a string.")
        if backend_config == "Auto":
            logger.info("Using auto-generated backend configuration")
            backend_config_content = get_backend_auto_config_s3_content(event)
        else:
            logger.info("Using provided backend configuration")
            backend_config_content = backend_config

        # Write backend properties to a file
        backend_file_path = "/tmp/config.generated.tfbackend"
        with open(backend_file_path, 'w') as backend_file:
            backend_file.write(backend_config_content)

        logger.info(f"Backend configuration written to {backend_file_path}")

        # Provide the backend config file to terraform
        init_cmd.extend(["-backend-config", backend_file_path])

    return init_cmd

def check_cloudformation_stack_status(stack_name):
    """
    Retrieves the current status of a CloudFormation stack.

    Args:
        stack_name (str): The name of the CloudFormation stack.

    Returns:
        str: The current status of the stack, or None if an error occurs.
    """
    client = boto3.client('cloudformation')

    try:
        response = client.describe_stacks(StackName=stack_name)
        stack_status = response['Stacks'][0]['StackStatus']
        return stack_status

    except client.exceptions.ClientError as e:
        print(f"Error checking stack status: {e}")
        return None

def terraform_has_provisioned_resources(terraform_binary, log_group=None, log_stream_name=None, environment=None):

    """
    Check if Terraform has provisioned any resources in the current state.

    Returns:
        bool: True if resources are provisioned, False otherwise.
    """
    try:
        # Use run_terraform_command to check the state list
        result = run_terraform_command(
            [terraform_binary, 'state', 'list'],
            log_group=log_group,
            log_stream_name=log_stream_name,
            environment=environment
        )

        # Check if there are any resources in the state
        resources = result.stdout.strip()

        if resources:
            logger.info("Terraform has provisioned the following resources:")
            logger.info(resources)
            return True
        else:
            logger.info("Terraform has not provisioned any resources.")
            return False

    except Exception as e:
        logger.error(f"An error occurred while checking Terraform state: {e}")
        return False

def execute_terraform_command(event, stack_status, terraform_binary, backend_contents, environment, log_group, log_stream_name):
    """
    Execute the appropriate Terraform command based on the request type.

    Args:
        event (dict): The Lambda event containing the request type, stack name, and other relevant information.
        stack_status (str): The current status of the CloudFormation stack.
        terraform_binary (str): The path to the Terraform binary.
        backend_contents (str): The contents of the Terraform backend configuration.
        environment (dict): The environment variables for the Terraform command.
        log_group (str): The CloudWatch log group name for logging Terraform output (optional).
        log_stream_name (str): The CloudWatch log stream name for logging Terraform output (optional).

    Returns:
        dict: The flattened Terraform outputs for the given request type.
    """
    try:
        request_type = event['RequestType']

        # Step 1: Run `terraform init` to initialize the backend and configuration
        logger.info("Initializing Terraform...")
        init_cmd = build_terraform_init_cmd(terraform_binary, backend_contents, event)
        run_terraform_command(init_cmd, log_group, log_stream_name, environment)

        # Step 2: Handle request types for apply or destroy
        if request_type in ['Create', 'Update']:
            logger.info(f"Running 'terraform apply' for {request_type}...")
            run_terraform_command([terraform_binary, "apply", "-auto-approve", "-no-color"], log_group, log_stream_name, environment)

            # Step 3: Capture and return Terraform outputs using run_terraform_command
            logger.info("Capturing Terraform outputs...")
            output_cmd = [terraform_binary, "output", "-json"]
            output_result = run_terraform_command(output_cmd, log_group, log_stream_name, environment)

            terraform_outputs = json.loads(output_result.stdout)
            flattened_outputs = {key: value['value'] for key, value in terraform_outputs.items()}
            logger.info(f"Flattened Terraform outputs: {flattened_outputs}")
            return flattened_outputs

        elif request_type == 'Delete':
            # Support cases when stack status is ROLLBACK_IN_PROGRESS and Terraform is misconfigured as a result of previous failed Create event
            if stack_status == "ROLLBACK_IN_PROGRESS":
                try:
                    if not terraform_has_provisioned_resources(terraform_binary, log_group, log_stream_name, environment):
                        logger.info("No provisioned terraform resources. Skipping 'terraform destroy'...")
                        return {}

                except Exception as e:
                    logger.info(f"Terraform misconfiguration: {e}")
                    logger.info("Misconfigured terraform state during deletion in rollback state. Skipping 'terraform destroy'...")
                    return {}

            # Otherwise, continue with the normal destroy operation
            logger.info("Running 'terraform destroy'...")
            run_terraform_command([terraform_binary, "destroy", "-auto-approve", "-no-color"], log_group, log_stream_name, environment)

            return {}

    except subprocess.CalledProcessError as e:
        logger.error(f"Error running Terraform command: {e}")
        raise

def validate_supported_resource_properties(resource_properties):
    """
    Validate that the provided resource properties only contain supported properties.

    Args:
        resource_properties (dict): The resource properties to validate.

    Raises:
        Exception: If any unsupported properties are found.
    """
    for prop in resource_properties.keys():
        if prop not in SUPPORTED_RESOURCE_PROPERTIES:
            raise Exception(f"Unsupported property: {prop}")

def setup_cloudwatch_logging(context, resource_properties):
    """
    Set up logging to CloudWatch Logs if ExecutionLogsTargetArn is provided.

    Args:
        context (object): The Lambda function context object.
        resource_properties (dict): The resource properties for the custom resource.

    Returns:
        tuple: A tuple containing (log_group, log_stream_name) if ExecutionLogsTargetArn is provided, otherwise None.
    """

    execution_logs_target_arn = resource_properties.get('ExecutionLogsTargetArn')
    log_group = None
    log_stream_name = None

    if execution_logs_target_arn:
        log_group = execution_logs_target_arn.split(':')[-2]
        log_stream_name = f"{context.log_stream_name}-terraform-output"

        # Ensure the log stream exists
        try:
            cloudwatch_logs.create_log_stream(logGroupName=log_group, logStreamName=log_stream_name)
        except ClientError as e:
            if e.response['Error']['Code'] != 'ResourceAlreadyExistsException':
                raise Exception(f"Error creating log stream: {e}")

    return log_group, log_stream_name

def handler(event, context):
    """
    Lambda function handler for CloudFormation custom resource.

    This function is responsible for executing Terraform commands based on the request type and properties passed in the event.

    :param event: The event object passed to the Lambda function.
    :type event: dict
    :param context: The Lambda function context object.
    :type context: object
    """
    logger.info(f"Event: {json.dumps(event)}")
    logger.info(f"Context: {context}")

    try:
        # Clean the .terraform directory cache
        clean_terraform_cache()

        stack_name = event['StackId'].split('/')[-2]
        stack_status = check_cloudformation_stack_status(stack_name)

        # Validate and extract properties
        resource_properties = event.get('ResourceProperties', {})

        if not (stack_status == "ROLLBACK_IN_PROGRESS" and event['RequestType'] == 'Delete'):
            validate_supported_resource_properties(resource_properties)
        else:
            send_response(event=event, context=context, response_status=cfnresponse.SUCCESS, response_data={})
            return

        # Set up environment variables for Terraform
        environment = setup_environment_variables(resource_properties)

        # Prepare Terraform configuration
        prepare_terraform_configuration(resource_properties)

        # Set up CloudWatch Logs, if required
        log_group, log_stream_name = setup_cloudwatch_logging(context, resource_properties)

        # Determine the request type and execute the appropriate Terraform command
        terraform_binary = '/var/task/terraform'
        backend_contents = resource_properties.get('Backend')
        flattened_outputs = execute_terraform_command(event, stack_status, terraform_binary, backend_contents, environment, log_group, log_stream_name)

        # Send success response
        send_response(event=event, context=context, response_status=cfnresponse.SUCCESS, response_data=flattened_outputs)

    except Exception as exception:
        logger.error(f"Unexpected error: {exception}", exc_info=True)
        send_response(event=event, context=context, response_status=cfnresponse.FAILED, response_reason=f"Reason: {str(exception)}")

