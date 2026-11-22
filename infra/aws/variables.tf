variable "region" {
  type    = string
  default = "us-east-1"
}

variable "env" {
  type    = string
  default = "demo"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "image_tag" {
  description = "Tag pushed to both ECR repositories (e.g. the git SHA)."
  type        = string
  default     = "latest"
}

variable "llm_mode" {
  description = "fake | replay | live. Public demo default is fake ($0); live needs a hosted model."
  type        = string
  default     = "fake"
}

variable "llm_default_model" {
  description = "provider:model used when llm_mode=live (a hosted provider; Ollama is not deployed)."
  type        = string
  default     = "ollama:gemma4:12b"
}

variable "llm_api_key_secret_arn" {
  description = "Optional Secrets Manager ARN holding the hosted model's API key (OPENAI_API_KEY or ANTHROPIC_API_KEY style)."
  type        = string
  default     = ""
}

variable "llm_api_key_env_name" {
  description = "Env var name the provider SDK reads for its key, e.g. OPENAI_API_KEY."
  type        = string
  default     = "OPENAI_API_KEY"
}

variable "certificate_arn" {
  description = "ACM certificate for HTTPS on the load balancer. Empty = HTTP only (demo)."
  type        = string
  default     = ""
}

variable "allowed_cidrs" {
  description = "Who can reach the load balancer."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "monthly_budget_usd" {
  description = "AWS Budgets alarm (spend cap) — notifies, does not stop resources."
  type        = number
  default     = 30
}

variable "budget_email" {
  description = "Where budget alerts go."
  type        = string
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
