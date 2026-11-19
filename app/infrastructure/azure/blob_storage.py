"""
Blob Storage Client (app/infrastructure/azure/blob_storage.py)
Azure Blob Storage service for exporting query artifacts and report PDFs.
"""

from typing import Optional
from app.core.logging import logger


class AzureBlobStorageService:
    def __init__(self, connection_string: Optional[str] = None, container_name: str = "reports"):
        self.connection_string = connection_string
        self.container_name = container_name

    async def upload_bytes(self, blob_name: str, data: bytes, content_type: str = "application/pdf") -> str:
        logger.info(f"Simulating upload of blob '{blob_name}' ({len(data)} bytes) to container '{self.container_name}'")
        return f"https://storage.azure.net/{self.container_name}/{blob_name}"
