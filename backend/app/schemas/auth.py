
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    mobile_number: str = Field(min_length=10, max_length=15)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    message: str
