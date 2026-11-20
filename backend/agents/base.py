import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, List
from models.state import AgentResult

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    name: str = "base"

    @abstractmethod
    def run(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        pass

    def execute(self, query: str, ticker: str, years: Optional[List[int]] = None, quarters: Optional[List[int]] = None, **kwargs) -> AgentResult:
        start = time.time()
        try:
            result = self.run(query, ticker, years, quarters, **kwargs)
            result.execution_time_ms = int((time.time() - start) * 1000)
            logger.info(f"{self.name} completed in {result.execution_time_ms}ms")
            return result
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"{self.name} failed after {elapsed}ms: {e}")
            return AgentResult(
                agent_name=self.name,
                response=f"Error in {self.name}: {str(e)}",
                confidence=0.0,
                sources=[],
                execution_time_ms=elapsed,
            )
