from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RunRequest(StrictModel):
    message: str = Field(min_length=1, max_length=6000)
    intent: Literal['deploy', 'diagnose', 'monitor', 'operate', 'repair'] = 'diagnose'
    project: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,62}$')
    source: str = Field(default='.', max_length=500)
    plan_id: str = Field(default='', max_length=100)
    bundle_plan_id: str = Field(default='',max_length=100)
    network: str = Field(default='',max_length=100)
    network_name: str = Field(default='',max_length=63)
    platform: str = Field(default='', max_length=30)
    port: int | None = Field(default=None, ge=1, le=65535)
    domain: str = Field(default='', max_length=253)
    health_url: str = Field(default='', max_length=1000)


class Step(StrictModel):
    action: Literal['create_network','create_app', 'deploy', 'restart', 'scale', 'resize', 'update_env',
                    'attach_domain', 'enable_ssl', 'create_zone', 'create_record', 'redeploy', 'repair_deploy']
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=1500)
    args: dict = Field(default_factory=dict)


class HostingOption(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    cost: str = Field(min_length=1, max_length=300)
    bundle_plan_id: str = Field(default='',max_length=100)
    network: str = Field(default='',max_length=100)
    network_id: str = Field(default='',max_length=100)
    network_name: str = Field(default='',max_length=63)


class Answer(StrictModel):
    message: str = Field(min_length=1,max_length=3000)
    port: int | None = Field(default=None,ge=1,le=65535)
    platform: str | None = Field(default=None,max_length=30)
    plan_id: str | None = Field(default=None,max_length=100)
    hosting_choice: str | None = Field(default=None,max_length=64)
    network: str | None = Field(default=None,max_length=100)
    bundle_plan_id: str | None = Field(default=None,max_length=100)
    network_name: str | None = Field(default=None,max_length=63)


class InputRequest(StrictModel):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,99}$')
    label: str = Field(min_length=1,max_length=120)
    hint: str = Field(default='',max_length=300)
    kind: Literal['secret','text','connection'] = 'secret'


class InputValues(StrictModel):
    values: dict[str,str] = Field(min_length=1,max_length=100)
    message: str = Field(default='',max_length=3000)
    plan_id: str | None = Field(default=None,max_length=100)
    hosting_choice: str | None = Field(default=None,max_length=64)
    network: str | None = Field(default=None,max_length=100)


class Plan(StrictModel):
    summary: str = Field(min_length=1, max_length=6000)
    inputs: list[InputRequest] = Field(default_factory=list,max_length=50)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    questions: list[str] = Field(default_factory=list, max_length=15)
    steps: list[Step] = Field(default_factory=list, max_length=12)

    @field_validator('questions', 'evidence', 'limitations')
    @classmethod
    def bound_text(cls, items):
        if any(len(item) > 3000 for item in items):
            raise ValueError('Plan text is too long')
        return items
