import base64
import hashlib
import hmac
import unittest

from crypto_trader.exchange.bitget import BitgetClient, UnsafeTradingConfiguration


class BitgetClientTests(unittest.TestCase):
    def test_signature(self) -> None:
        payload = "123POST/api/v2/mix/order/place-order{\"symbol\":\"BTCUSDT\"}"
        expected = base64.b64encode(
            hmac.new(b"secret", payload.encode(), hashlib.sha256).digest()
        ).decode()
        self.assertEqual(
            BitgetClient.signature(
                "secret",
                "123",
                "POST",
                "/api/v2/mix/order/place-order",
                '{"symbol":"BTCUSDT"}',
            ),
            expected,
        )

    def test_live_endpoint_requires_explicit_unlock(self) -> None:
        with self.assertRaises(UnsafeTradingConfiguration):
            BitgetClient(demo_mode=False, live_trading_enabled=False)

    def test_demo_header_is_attached(self) -> None:
        from crypto_trader.exchange.bitget import BitgetCredentials

        client = BitgetClient(BitgetCredentials("key", "secret", "pass"), demo_mode=True)
        headers = client._headers("GET", "/api/test", "")
        self.assertEqual(headers["paptrading"], "1")

    def test_private_request_is_blocked_in_paper_configuration(self) -> None:
        client = BitgetClient(demo_mode=True)
        client.demo_mode = False
        with self.assertRaises(UnsafeTradingConfiguration):
            client.private_request("POST", "/api/v2/mix/order/place-order", {})


if __name__ == "__main__":
    unittest.main()
