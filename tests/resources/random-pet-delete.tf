terraform {
  backend "s3" {}

  required_providers {
    random = {
      source = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

variable "pet_name_prefix" {
  type = string
  default = "Mr."
}
