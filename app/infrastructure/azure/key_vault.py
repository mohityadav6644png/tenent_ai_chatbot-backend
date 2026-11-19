"""
Key Vault Client (app/infrastructure/azure/key_vault.py)
Azure Key Vault secret provider with local fallback to application settings.
"""

from typing import Optional
from app.core.config import settings
from app.core.logging import logger


class KeyVaultClient:
    """Retrieves application secrets from Azure Key Vault or environment settings."""

    def __init__(self, vault_url: Optional[str] = None):
        self.vault_url = vault_url
        self._client = None
        if self.vault_url:
            try:
                from azure.identity import DefaultAzureCredential
                from azure.keyvault.secrets import SecretClient
                credential = DefaultAzureCredential()
                self._client = SecretClient(vault_url=self.vault_url, credential=credential)
                logger.info(f"Connected to Azure Key Vault at {self.vault_url}")
            except Exception as exc:
                logger.warning(f"Could not connect to Azure Key Vault: {exc}")

    def get_secret(self, secret_name: str, fallback_value: Optional[str] = None) -> Optional[str]:
        if self._client:
            try:
                secret = self._client.get_secret(secret_name)
                return secret.value
            except Exception as exc:
                logger.warning(f"Failed to fetch secret '{secret_name}' from Key Vault: {exc}")
        return getattr(settings, secret_name.upper(), fallback_value)
