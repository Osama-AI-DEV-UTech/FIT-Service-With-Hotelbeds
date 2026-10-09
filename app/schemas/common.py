from typing import Any

from pydantic import BaseModel, ConfigDict


class HBModel(BaseModel):
    """Base for Hotelbeds pass-through request models.

    Field names mirror Hotelbeds' JSON exactly (camelCase). Unknown fields
    are ALLOWED and forwarded untouched, so newly-released Hotelbeds
    fields work without a code change.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, by_alias=True)
