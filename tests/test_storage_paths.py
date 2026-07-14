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
        for payment_method in ("ideal", "pix", "kakao_pay", "twint", "upi"):
            with self.subTest(payment_method=payment_method):
                self.assertIsNotNone(ideal_ui.manual_proxy_paths(payment_method))

    def test_pix_uses_one_br_proxy_pool_for_all_stages(self) -> None:
        primary_path, promotion_path = ideal_ui.manual_proxy_paths("pix")

        self.assertEqual(primary_path, promotion_path)
        self.assertEqual(ideal_ui.PAYMENT_CHAIN_DEFAULTS["pix"], ("BR", "BR", "BR"))
        self.assertEqual(ideal_ui.PAYMENT_METHODS["pix"]["flow"], "BR/BR/BR")


if __name__ == "__main__":
    unittest.main()
