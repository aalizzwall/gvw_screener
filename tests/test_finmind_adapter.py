import unittest
from unittest.mock import patch, MagicMock
from src.providers.finmind_adapter import FinMindAdapter

class TestFinMindAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = FinMindAdapter()

    @patch('src.providers.finmind_adapter.requests.get')
    def test_check_availability_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": 200, "data": [{"stock_id": "2330", "stock_name": "台積電"}]}
        mock_get.return_value = mock_resp

        self.assertTrue(self.adapter.check_availability())

    @patch('src.providers.finmind_adapter.requests.get')
    def test_fetch_stock_data_normalization(self, mock_get):
        # Setup mock responses for different datasets
        def mock_side_effect(url, params, timeout):
            resp = MagicMock()
            resp.status_code = 200
            dataset = params.get("dataset")
            if dataset == "TaiwanStockFinancialStatements":
                resp.json.return_value = {
                    "data": [
                        {"type": "Revenue", "value": 1000000},
                        {"type": "IncomeAfterTaxes", "value": 300000},
                        {"type": "GrossProfit", "value": 500000},
                        {"type": "EPS", "value": 5.5}
                    ]
                }
            elif dataset == "TaiwanStockBalanceSheet":
                resp.json.return_value = {
                    "data": [
                        {"type": "TotalAssets", "value": 2000000},
                        {"type": "Liabilities", "value": 800000},
                        {"type": "Equity", "value": 1200000}
                    ]
                }
            elif dataset == "TaiwanStockPER":
                resp.json.return_value = {
                    "data": [
                        {"PER": 18.5, "PBR": 2.1, "dividend_yield": 3.2}
                    ]
                }
            elif dataset == "TaiwanStockInfo":
                resp.json.return_value = {
                    "data": [
                        {"industry_category": "半導體業", "stock_name": "台積電", "type": "twse"}
                    ]
                }
            return resp

        mock_get.side_effect = mock_side_effect

        data = self.adapter.fetch_stock_data("2330")
        self.assertEqual(data["ticker"], "2330")
        self.assertEqual(data["financials"]["income_statement"]["revenue"], 1000000.0)
        self.assertEqual(data["financials"]["income_statement"]["net_income"], 300000.0)
        self.assertEqual(data["financials"]["balance_sheet"]["total_assets"], 2000000.0)
        self.assertEqual(data["financials"]["balance_sheet"]["total_liabilities"], 800000.0)
        self.assertEqual(data["basic_info"]["pe"], 18.5)
        self.assertEqual(data["basic_info"]["industry"], "半導體業")

    @patch('src.providers.finmind_adapter.requests.get')
    def test_fetch_market_index(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"date": "2026-10-13", "close": 23000.5, "open": 22900.0, "max": 23100.0, "min": 22850.0, "Trading_Volume": 100000000}
            ]
        }
        mock_get.return_value = mock_resp

        index_data = self.adapter.fetch_market_index("TAIEX")
        self.assertEqual(index_data["index_ticker"], "TAIEX")
        self.assertEqual(index_data["price"], 23000.5)

    @patch('src.providers.finmind_adapter.requests.get')
    def test_fetch_price_history(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [
                {"date": "2026-10-13", "open": 1000.0, "max": 1020.0, "min": 995.0, "close": 1015.0, "Trading_Volume": 30000000}
            ]
        }
        mock_get.return_value = mock_resp

        prices = self.adapter.fetch_price_history("2330", days=5)
        self.assertEqual(len(prices), 1)
        self.assertEqual(prices[0]["close"], 1015.0)
        self.assertEqual(prices[0]["volume"], 30000000)

if __name__ == "__main__":
    unittest.main()

