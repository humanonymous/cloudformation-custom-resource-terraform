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

# Create a resource with random pet name
resource "random_pet" "pet_name" {
  prefix = var.pet_name_prefix
  separator = " "
}

output "pet" {
  value = random_pet.pet_name.id
}
