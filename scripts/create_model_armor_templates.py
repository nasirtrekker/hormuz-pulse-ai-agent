"""Create or update the two decoupled Model Armor templates.

Usage: python scripts/create_model_armor_templates.py PROJECT_ID LOCATION
Confidence levels are tunable via env vars (HIGH | MEDIUM_AND_ABOVE | LOW_AND_ABOVE):
  MA_PI_CONFIDENCE   prompt injection / jailbreak, input template (default LOW_AND_ABOVE)
  MA_RAI_CONFIDENCE  responsible-AI filters, both templates      (default MEDIUM_AND_ABOVE)
Needs roles/modelarmor.admin and modelarmor.googleapis.com enabled.
"""
import os
import sys

from google.api_core.exceptions import AlreadyExists
from google.cloud import modelarmor_v1 as ma

INPUT_ID = "hormuz-input"
OUTPUT_ID = "hormuz-output"
LEVELS = {"HIGH", "MEDIUM_AND_ABOVE", "LOW_AND_ABOVE"}
RAI_TYPES = ["SEXUALLY_EXPLICIT", "HATE_SPEECH", "HARASSMENT", "DANGEROUS"]


def level(env: str, default: str) -> str:
    value = os.environ.get(env, default)
    if value not in LEVELS:
        sys.exit(f"{env} must be one of {sorted(LEVELS)}")
    return value


def build(pi_level: str, rai_level: str) -> dict:
    rai = {"rai_filters": [{"filter_type": t, "confidence_level": rai_level} for t in RAI_TYPES]}
    sdp = {"basic_config": {"filter_enforcement": "ENABLED"}}
    uri = {"filter_enforcement": "ENABLED"}
    return {
        INPUT_ID: {"filter_config": {
            "pi_and_jailbreak_filter_settings": {"filter_enforcement": "ENABLED",
                                                 "confidence_level": pi_level},
            "malicious_uri_filter_settings": uri, "sdp_settings": sdp, "rai_settings": rai}},
        OUTPUT_ID: {"filter_config": {
            "malicious_uri_filter_settings": uri, "sdp_settings": sdp, "rai_settings": rai}},
    }


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    project, location = sys.argv[1:]
    client = ma.ModelArmorClient(
        transport="rest", client_options={"api_endpoint": f"modelarmor.{location}.rep.googleapis.com"})
    parent = f"projects/{project}/locations/{location}"
    templates = build(level("MA_PI_CONFIDENCE", "LOW_AND_ABOVE"),
                      level("MA_RAI_CONFIDENCE", "MEDIUM_AND_ABOVE"))
    for template_id, body in templates.items():
        template = ma.Template(**body)
        try:
            client.create_template(request=ma.CreateTemplateRequest(
                parent=parent, template_id=template_id, template=template))
            print(f"created {template_id}")
        except AlreadyExists:
            template.name = f"{parent}/templates/{template_id}"
            client.update_template(request=ma.UpdateTemplateRequest(template=template))
            print(f"updated {template_id}")


if __name__ == "__main__":
    main()
