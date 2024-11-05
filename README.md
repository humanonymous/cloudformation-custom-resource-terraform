# CloudFormation Custom Resource - Terraform Integration

## Rationale

The integration of Terraform within a CloudFormation Custom Resource addresses the need for greater flexibility in infrastructure management. While AWS CloudFormation is great for managing AWS resources, it's confined to the AWS ecosystem. Terraform, however, offers a wide range of providers, enabling seamless management of resources from third-party services. These include on-premises servers, external databases, monitoring tools, and CI/CD systems. This approach unifies resource management by leveraging CloudFormation's native AWS integration while extending its capabilities through Terraform's extensive provider ecosystem. The result is a more comprehensive and automated infrastructure provisioning process.

## Solution Overview

This solution leverages Lambda-backed CloudFormation Custom Resources to seamlessly integrate Terraform configurations into CloudFormation templates. It offers a Lambda function—designated as the ServiceToken in custom resources—that executes Terraform's provisioning logic.

See [CloudFormation Custom Resources Guide](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/template-custom-resources.html) for more details.

## Setup Instructions

1. Clone the repository:

    ```bash
    git clone https://github.com/humanonymous/cloudformation-custom-resource-terraform.git
    cd cloudformation-custom-resource-terraform
    ```

2. Install **go-task** from [https://taskfile.dev](https://taskfile.dev/). It's a standalone binary with no dependencies that elegantly wraps shell scripts. For more details, refer to [Taskfile.yaml](Taskfile.yaml). You can also copy scripts from the file and run them manually—the code is pretty straightforward.
3. Configure [Taskfile.env](Taskfile.env) to set up your environment variables. These variables specify function name, S3 bucket used for CloudFormation stack deployment, and other essential parameters. Refer to the file for more details.
4. Execute the following commands to build and deploy the CloudFormation Custom Resource:

    ```bash
    task clean build deploy --silent
    ```

## General Usage

Create CloudFormation Custom Resources in your infrastructure code. Ensure you use the Lambda ARN in the custom resource configuration's `ServiceToken` property.

```yaml
Resources:
  CustomTerraformConfigurationExample:
    Type: Custom::TerraformConfiguration
    Properties:
      ServiceToken: <ARN of Lambda>
      Configuration: |
        terraform {
          backend "s3" {
            bucket         = "mybucket"
            key            = "path/to/my/key"
            region         = "us-east-1"
            dynamodb_table = "TableName"
          }
        }
        resource "aws_s3_bucket" "example" {
          bucket = "example-bucket"
        }
        ...
```

## Property Reference

### ServiceToken (Required)

The ARN of the Lambda function handling the custom resource logic. The ARN is generated during setup and must be always provided in the custom resource definitions.

```yaml
Resources:
  CustomTerraformConfigurationExample:
    Type: Custom::TerraformConfiguration
    Properties:
      ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
      # Other properties...
```

**Note:** The `ServiceToken` property can't be changed after resource creation. Check the "Potential Drawbacks or Limitations" section for more details.

### Backend (Optional)

Specifies the Terraform backend. Supported options:

- `Auto`: We currently support automatic configuration of an S3 backend with a DynamoDB table for state locking. This is the recommended method for implementing consistent Terraform state isolation per CloudFormation resource. To use the `Auto` configured backend, you'll need to set two additional environment variables in [Taskfile.env](Taskfile.env):
  - `STACK_PARAM_TERRAFORM_BACKEND_AUTO_S3_BUCKET` - The bucket to store Terraform states. This bucket should be accessible by CloudFormation during stack deployment.
  - `STACK_PARAM_TERRAFORM_BACKEND_AUTO_S3_DYNAMODB_TABLE` - The DynamoDB table for state locking and consistency checking.
- **Partial Configuration**: Provide a partial Terraform backend configuration as defined in the [Backend Partial Configuration](https://developer.hashicorp.com/terraform/language/backend#partial-configuration) guide.
- **Inline Configuration:** You can also specify backend configuration directly in the `Configuration` property (see the corresponding section below).

**Note:** If not specified, Terraform will use a `local` backend. This prevents the custom resource from preserving state between **Create**, **Update**, and **Delete** actions, leading to CloudFormation lifecycle failures. Always provide a backend configuration to ensure proper state management.

- Automatically configured backend. The `terraform { backend “s3” {}}` block is required.

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Backend: "Auto"
          Configuration: |
            terraform {
              backend "s3" {}
            }
            resource "aws_s3_bucket" "example" {
              bucket = "example-bucket"
            }
    ```

- Partial backend configuration

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Backend: |
            address = "demo.consul.io"
            scheme  = "https"
            path    = "path/to/terraform/state"
          Configuration: |
            terraform {
              backend "consul" {}
            }
            resource "aws_s3_bucket" "example" {
              bucket = "example-bucket"
            }
    ```

- Inline backend configuration

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Configuration: |
            terraform {
              backend "consul" {
                address = "demo.consul.io"
                scheme  = "https"
                path    = "path/to/terraform/state"
              }
            }
            resource "aws_s3_bucket" "example" {
              bucket = "example-bucket"
            }
    ```

### Environment (Optional)

A map of environment variables to be set during Terraform execution. For more details, refer to [Terraform's Environment Variables Guide](https://developer.hashicorp.com/terraform/cli/config/environment-variables).

```yaml
Resources:
  CustomTerraformConfigurationExample:
    Type: Custom::TerraformConfiguration
    Properties:
      ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
      Backend: "Auto"
      Environment:
        TF_LOG: "TRACE"
        TF_VAR_bucket_name: "example-bucket"
      Configuration: |
        terraform {
          backend "s3" {}
        }
        variable "bucket_name" {
          type = string
        }
        resource "aws_s3_bucket" "example" {
          bucket = var.bucket_name
        }
```

**Note:** The `TF_IN_AUTOMATION` and `TF_INPUT` environment variables are automatically set and cannot be overridden for Custom CloudFormation Resources.

### Configuration (Required)

Specifies the Terraform configuration in one of the following formats:

- Inline Terraform configuration:

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Backend: "Auto"
          Configuration: |
            terraform {
              backend "s3" {}
            }
            resource "aws_s3_bucket" "example" {
              bucket = "example-bucket"
            }
    ```

- HTTP(S) URL pointing to a Terraform configuration file:

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Backend: "Auto"
          Configuration: "https://example.com/path/to/terraform.tf"
    ```

- S3 URL pointing to a Terraform configuration file:

    ```yaml
    Resources:
      CustomTerraformConfigurationExample:
        Type: Custom::TerraformConfiguration
        Properties:
          ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
          Backend: "Auto"
          Configuration: "s3://example/path/to/terraform.tf"
    ```

### Variables (Optional)

A map of Terraform variables to pass to the configuration:

```yaml
Resources:
  CustomTerraformConfigurationExample:
    Type: Custom::TerraformConfiguration
    Properties:
      ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
      Backend: "Auto"
      Configuration: |
        terraform {
          backend "s3" {}
        }
        variable "String" {
          type = string
        }
        variable "List" {
          type = list
        }
        variable "Number" {
          type = number
        }
        resource "aws_s3_bucket" "example" {
          bucket = var.String
        }
      Variables:
        String: "example-bucket"
        List:
          - "item1"
          - "item2"
        Number: 5
```

### ExecutionLogsTargetArn (Optional)

The ARN of a CloudWatch Log Group where Terraform execution logs will be sent.

## Outputs

The Custom Resource automatically maps Terraform outputs to CloudFormation outputs, allowing seamless integration between Terraform-managed resources and the rest of your CloudFormation stack.

```yaml
Resources:
  CustomTerraformConfigurationExample:
    Type: Custom::TerraformConfiguration
    Properties:
      ServiceToken: "arn:aws:lambda:us-east-1:123456789012:function:cloudformation-custom-resource-terraform"
      Backend: "Auto"
      Configuration: |
        terraform {
          backend "s3" {}
        }
        resource "aws_s3_bucket" "example" {
          bucket = "example-bucket"
        }
        output "S3BucketName" {
          value = var.aws_s3_bucket.id
        }

Outputs:
  S3BucketName: !GetAtt TerraformManagedResource.S3BucketName
```

## Potential Drawbacks or Limitations

- Modifying the `ServiceToken` value in custom resources requires replacing the entire custom resource. CloudFormation doesn't allow changes to this property and will fail with the error "Modifying service token is not allowed." This limitation means you'll need to recreate the resource if you want to change the underlying Lambda function.
- Limited access to Terraform state can make troubleshooting difficult. Developers may struggle to diagnose issues or track changes without direct access to the state managed by the CloudFormation Custom Resource.

## Testing

1. Install **go-task** from [https://taskfile.dev](https://taskfile.dev/).
2. Configure the relevant section of [Taskfile.env](Taskfile.env) to set up your environment variables.
3. Execute the following commands to build and deploy the CloudFormation Custom Resource:

```bash
task test --silent
```
