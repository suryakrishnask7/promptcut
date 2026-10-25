"""
src/promptcut/storage/s3.py

IncuBrix Integration Standard #9:
S3/MinIO compatible storage adapter using S3 API (boto3 client).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from promptcut.storage.base import StorageAdapter


def create_s3_client(
    endpoint_url: str | None = None,
    aws_access_key_id: str | None = None,
    aws_secret_access_key: str | None = None,
    region_name: str | None = None,
):
    """Factory creating a boto3 S3 client with optional custom endpoint (for MinIO)."""
    try:
        import boto3
    except ImportError:
        raise ImportError(
            "boto3 is required for S3/MinIO storage backend. Install via: pip install boto3"
        )

    endpoint = endpoint_url or os.getenv("S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL")
    access_key = aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID", "minioadmin")
    secret_key = aws_secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
    region = region_name or os.getenv("AWS_REGION", "us-east-1")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
    )


class S3StorageAdapter(StorageAdapter):
    """S3 / MinIO storage adapter."""

    def __init__(
        self,
        bucket_name: str | None = None,
        endpoint_url: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket_name = bucket_name or os.getenv("S3_BUCKET", "promptcut-media")
        self.endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT_URL") or os.getenv("AWS_ENDPOINT_URL")
        self._client = s3_client

    @property
    def client(self):
        if self._client is None:
            self._client = create_s3_client(endpoint_url=self.endpoint_url)
        return self._client

    def save_file(self, key: str, data: bytes | Path, content_type: str | None = None) -> str:
        body: bytes
        if isinstance(data, Path):
            body = data.read_bytes()
        else:
            body = data

        clean_key = key.lstrip("/\\")
        kwargs: dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": clean_key,
            "Body": body,
        }
        if content_type:
            kwargs["ContentType"] = content_type

        self.client.put_object(**kwargs)
        return f"s3://{self.bucket_name}/{clean_key}"

    def get_file(self, key: str) -> bytes:
        clean_key = key.lstrip("/\\")
        response = self.client.get_object(Bucket=self.bucket_name, Key=clean_key)
        return response["Body"].read()

    def file_exists(self, key: str) -> bool:
        clean_key = key.lstrip("/\\")
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=clean_key)
            return True
        except Exception:
            return False

    def delete_file(self, key: str) -> None:
        clean_key = key.lstrip("/\\")
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=clean_key)
        except Exception:
            pass

    def get_url(self, key: str) -> str:
        clean_key = key.lstrip("/\\")
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": clean_key},
                ExpiresIn=3600,
            )
        except Exception:
            base = self.endpoint_url.rstrip("/") if self.endpoint_url else "https://s3.amazonaws.com"
            return f"{base}/{self.bucket_name}/{clean_key}"

    def is_ready(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return True
        except Exception:
            return False
