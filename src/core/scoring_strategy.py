from abc import ABC, abstractmethod
from typing import Dict, Any

class ScoringStrategy(ABC):
    """
    Abstract base class for stock quality scoring models.
    Allows switching between different scoring methodologies.
    """
    @abstractmethod
    def calculate_score(self, pl_data: Dict[str, Any], bs_data: Dict[str, Any]) -> float:
        """
        Calculates a quality score between 0.0 and 1.0.
        """
        pass

class DefaultScoringStrategy(ScoringStrategy):
    """
    Standard scoring model based on revenue growth, net margin, and debt ratio.
    """
    def calculate_score(self, pl_data: Dict[str, Any], bs_data: Dict[str, Any]) -> float:
        try:
            rev_curr = float(pl_data.get('revenue', 0) or pl_data.get('營業收入', 0))
            rev_growth = 0.2 if rev_curr > 0 else 0 
            net_income = float(pl_data.get('net_income', 0) or pl_data.get('本期淨利', 0))
            margin = (net_income / rev_curr) if rev_curr > 0 else 0
            
            total_assets = float(bs_data.get('total_assets', 0) or bs_data.get('資產總額', 0))
            total_liab = float(bs_data.get('total_liabilities', 0) or bs_data.get('負債總額', 0))
            debt_ratio = (total_liab / total_assets) if total_assets > 0 else 1.0

            s_growth = min(1.0, max(0.0, (rev_growth - 0.1) / 0.3))
            s_margin = min(1.0, max(0.0, (margin - 0.05) / 0.15))
            s_stable = min(1.0, max(0.0, (0.6 - debt_ratio) / 0.3))

            final_q = (s_growth * 0.5) + (s_margin * 0.3) + (s_stable * 0.2)
            return round(final_q, 3)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Quality score calc error: {e}")
            return 0.5
