from pathlib import Path
import unittest
from unittest.mock import patch

import ideal_ui


class StoragePathTests(unittest.TestCase):
    def test_directory_bind_mount_uses_nested_file(self) -> None:
        mounted_path = Path("/app/token.txt")
        with patch.object(Path, "is_dir", return_value=True):
            resolved_path = ideal_ui.storage_file_path(mounted_path)

        self.assertEqual(resolved_path, mounted_path / "token.txt")

    def test_ideal_storage_paths_normalize_accidental_directories(self) -> None:
        proxy_path = Path("/app/nl_proxy_seeds.txt")
        token_path = Path("/app/token.txt")
        with patch.object(Path, "is_dir", return_value=True):
            with (
                patch.object(ideal_ui, "IDEAL_PRIMARY_PROXY_SEED_PATH", proxy_path),
                patch.object(ideal_ui, "TOKEN_PATH", token_path),
            ):
                resolved_proxy, resolved_token = ideal_ui.payment_storage_paths("ideal")

        self.assertEqual(resolved_proxy, proxy_path / proxy_path.name)
        self.assertEqual(resolved_token, token_path / token_path.name)

    def test_manual_proxy_paths_exist_for_supported_payment_methods(self) -> None:
        for payment_method in ("ideal", "turkey_card", "pix", "kakao_pay", "twint", "upi"):
            with self.subTest(payment_method=payment_method):
                self.assertIsNotNone(ideal_ui.manual_proxy_paths(payment_method))

    def test_pix_uses_two_br_proxy_pools(self) -> None:
        primary_path, promotion_path = ideal_ui.manual_proxy_paths("pix")

        self.assertNotEqual(primary_path, promotion_path)
        self.assertEqual(primary_path.name, "br_proxy_seeds.txt")
        self.assertEqual(promotion_path.name, "vn_proxy_seeds.txt")
        self.assertEqual(ideal_ui.PAYMENT_CHAIN_DEFAULTS["pix"], ("BR", "BR", "BR"))
        self.assertEqual(ideal_ui.PAYMENT_METHODS["pix"]["flow"], "BR/BR/BR")

    def test_pix_environment_maps_both_br_proxy_files(self) -> None:
        primary_path = Path("C:/proxy/primary.txt")
        promotion_path = Path("C:/proxy/promotion.txt")
        payload = {
            "token": "test-token",
            "proxy_seed_file": str(primary_path),
            "manual_checkout_proxy_file": str(primary_path),
            "manual_provider_proxy_file": str(primary_path),
            "manual_promotion_proxy_file": str(promotion_path),
            "bootstrap_country": "VN",
            "promotion_country": "VN",
            "provider_country": "VN",
        }

        with patch.object(Path, "is_file", return_value=True):
            env, _ = ideal_ui.build_environment(
                payload,
                "pix",
                ideal_ui.PAYMENT_METHODS["pix"],
            )

        self.assertEqual(env["PIX_CHECKOUT_PROXY_FILE"], str(primary_path.resolve()))
        self.assertEqual(env["PIX_PROVIDER_PROXY_FILE"], str(primary_path.resolve()))
        self.assertEqual(env["PIX_PROMOTION_PROXY_FILE"], str(promotion_path.resolve()))
        self.assertEqual(env["PIX_BOOTSTRAP_COUNTRY"], "BR")
        self.assertEqual(env["PIX_PROMOTION_COUNTRY"], "BR")
        self.assertEqual(env["PIX_PROVIDER_COUNTRY"], "BR")

    def test_turkey_card_storage_and_chain(self) -> None:
        primary_path, promotion_path = ideal_ui.manual_proxy_paths("turkey_card")
        proxy_path, token_path = ideal_ui.payment_storage_paths("turkey_card")

        self.assertEqual(primary_path, ideal_ui.TURKEY_CARD_TR_PROXY_SEED_PATH)
        self.assertEqual(promotion_path, ideal_ui.TURKEY_CARD_GB_PROXY_SEED_PATH)
        self.assertEqual(proxy_path, ideal_ui.TURKEY_CARD_TR_PROXY_SEED_PATH)
        self.assertEqual(token_path, ideal_ui.TURKEY_CARD_TOKEN_PATH)
        self.assertEqual(
            ideal_ui.PAYMENT_CHAIN_DEFAULTS["turkey_card"],
            ("TR", "GB", "TR"),
        )

    def test_turkey_card_environment_is_fixed_and_secret_free(self) -> None:
        payload = {
            "token": "test-token",
            "proxy_seed_file": str(ideal_ui.TURKEY_CARD_TR_PROXY_SEED_PATH),
            "manual_checkout_proxy_file": str(ideal_ui.TURKEY_CARD_TR_PROXY_SEED_PATH),
            "manual_provider_proxy_file": str(ideal_ui.TURKEY_CARD_TR_PROXY_SEED_PATH),
            "manual_promotion_proxy_file": str(ideal_ui.TURKEY_CARD_GB_PROXY_SEED_PATH),
            "bootstrap_country": "US",
            "promotion_country": "US",
            "provider_country": "US",
            "card_number": "4242 4242 4242 4242",
            "card_exp_month": "12",
            "card_exp_year": "2030",
            "card_cvc": "123",
            "card_holder": "Emir Yilmaz",
        }

        with patch.object(Path, "is_file", return_value=True):
            env, public_config = ideal_ui.build_environment(
                payload,
                "turkey_card",
                ideal_ui.PAYMENT_METHODS["turkey_card"],
            )

        self.assertEqual(env["IDEAL_BOOTSTRAP_COUNTRY"], "TR")
        self.assertEqual(env["IDEAL_PROMOTION_COUNTRY"], "GB")
        self.assertEqual(env["IDEAL_PROVIDER_COUNTRY"], "TR")
        self.assertEqual(env["IDEAL_STRIPE_PAYMENT_METHOD"], "card")
        self.assertEqual(env["TURKEY_CARD_NUMBER"], "4242424242424242")
        self.assertNotIn("card_number", public_config)
        self.assertNotIn("card_cvc", public_config)


if __name__ == "__main__":
    unittest.main()
