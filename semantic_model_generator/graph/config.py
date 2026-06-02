from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV = str(Path(__file__).resolve().parent.parent / ".env")


class _RS(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV, env_prefix="REDSHIFT_", extra="ignore")
    user: str
    password: str
    host: str
    port: int = 5439
    db: str
    schema_name: str = Field("lpp", alias="REDSHIFT_SCHEMA")

    @property
    def schema(self) -> str:
        return self.schema_name

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{quote_plus(self.password)}@{self.host}:{self.port}/{self.db}"


class _Neo4j(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV, env_prefix="NEO4J_", extra="ignore")
    uri: str
    user: str
    password: str
    db: str

    def driver(self):
        from neo4j import GraphDatabase
        return GraphDatabase.driver(self.uri, auth=(self.user, self.password))


class _Bedrock(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV, extra="ignore")
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-west-2"
    aws_bedrock_sonnet_arn: str
    aws_bedrock_haiku_arn: str
    aws_bedrock_cohere_embed_v4_arn: str


rs = _RS()
neo4j = _Neo4j()
bedrock = _Bedrock()
