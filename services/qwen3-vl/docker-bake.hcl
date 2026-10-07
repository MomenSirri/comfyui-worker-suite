variable "DOCKERHUB_REPO" {
  default = "momensirri"
}

variable "DOCKERHUB_IMG" {
  default = "qwen3-vl-32b"
}

# A push to an existing tag replaces it. Set the next free tag for every release.
variable "RELEASE_VERSION" {
  default = "v11"
}

group "default" {
  targets = ["qwen3-vl"]
}

target "qwen3-vl" {
  context    = "."
  dockerfile = "Dockerfile"
  platforms  = ["linux/amd64"]
  tags       = ["${DOCKERHUB_REPO}/${DOCKERHUB_IMG}:${RELEASE_VERSION}"]
}
