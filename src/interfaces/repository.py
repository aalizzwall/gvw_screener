from abc import ABC, abstractmethod
from typing import Any, Dict

class DataRepository(ABC):
    @abstractmethod
    async def save_result(self, result: Dict[str, Any]) -> None:
        """Save a scoring result to the repository."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the repository connection."""
        pass


    @abstractmethod
    async def close(self) -> None:
        """Close the repository connection."""
        pass
