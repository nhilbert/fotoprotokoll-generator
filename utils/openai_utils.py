"""Shared utilities for OpenAI API integration."""


def strict_schema(schema: dict) -> dict:
    """Force all properties into `required[]` and set additionalProperties: false.

    OpenAI Structured Outputs strict mode requires:
    - Every property listed in required[] (including those with defaults)
    - additionalProperties: false at every object level
    - Nullable fields as anyOf: [{type: X}, {type: null}]  ← Pydantic generates this correctly

    Handles nested models defined in $defs.
    Applied via: model_config = ConfigDict(json_schema_extra=strict_schema)
    """
    schema["required"] = list(schema.get("properties", {}).keys())
    schema["additionalProperties"] = False
    for defn in schema.get("$defs", {}).values():
        defn["required"] = list(defn.get("properties", {}).keys())
        defn.setdefault("additionalProperties", False)
    return schema
