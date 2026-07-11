import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("GITHUB_PROXY_DB", ":memory:")

import app
from bs4 import BeautifulSoup


class AssetFragmentExpansionTests(unittest.TestCase):
    def test_expanded_asset_fragment_url_accepts_data_url(self):
        soup = BeautifulSoup(
            '<react-partial data-url="/owner/repo/releases/expanded_assets/v1.0.0"></react-partial>',
            "html.parser",
        )

        self.assertEqual(
            app.expanded_asset_fragment_url(soup.find("react-partial")),
            "https://github.com/owner/repo/releases/expanded_assets/v1.0.0",
        )

    def test_expand_asset_fragments_replaces_data_url_partial(self):
        soup = BeautifulSoup(
            '<main><react-partial data-url="/owner/repo/releases/expanded_assets/v1.0.0"></react-partial></main>',
            "html.parser",
        )
        upstream = Mock(
            status_code=200,
            text='<a href="/owner/repo/releases/download/v1.0.0/file.zip">file.zip</a>',
        )

        with patch("app.fetch_github", return_value=upstream) as fetch_github:
            app.expand_asset_fragments(soup)

        fetch_github.assert_called_once_with("https://github.com/owner/repo/releases/expanded_assets/v1.0.0")
        self.assertIsNone(soup.find("react-partial"))
        self.assertTrue(app.page_has_asset_downloads(soup, "owner", "repo", "v1.0.0"))


if __name__ == "__main__":
    unittest.main()
