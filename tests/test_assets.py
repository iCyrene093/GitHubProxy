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

    def test_expanded_asset_fragment_url_uses_valid_fallback_attribute(self):
        soup = BeautifulSoup(
            (
                '<react-partial data-url="/owner/repo/releases/metadata/v1.0.0" '
                'data-href="/owner/repo/releases/expanded_assets/v1.0.0"></react-partial>'
            ),
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

class ReleaseListAssetFallbackTests(unittest.TestCase):
    def test_append_missing_asset_fragments_for_release_list_fetches_each_empty_release(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a></h2>
                <summary>Assets 2</summary>
                <include-fragment>Loading</include-fragment>
              </section>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v0.9.0">v0.9.0</a></h2>
                <summary>Assets 1</summary>
                <a href="/owner/repo/releases/download/v0.9.0/existing.zip">existing.zip</a>
              </section>
            </body></html>
            """,
            "html.parser",
        )

        def fake_fetch(url):
            self.assertEqual(url, "https://github.com/owner/repo/releases/expanded_assets/v1.0.0")
            return Mock(
                status_code=200,
                text='<a href="/owner/repo/releases/download/v1.0.0/new.zip">new.zip</a>',
            )

        with patch("app.fetch_github", side_effect=fake_fetch) as fetch_github:
            app.append_missing_asset_fragments_for_release_list(soup, "owner", "repo")

        fetch_github.assert_called_once()
        sections = soup.find_all("section")
        self.assertTrue(app.page_has_asset_downloads(sections[0], "owner", "repo", "v1.0.0"))
        self.assertEqual(len(sections[0].select(".github-proxy-expanded-assets")), 1)
        self.assertEqual(len(sections[1].select(".github-proxy-expanded-assets")), 0)

    def test_append_missing_asset_fragments_ignores_body_links_to_other_tags(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v2.0.0">v2.0.0</a></h2>
                <p>See <a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a> for older notes.</p>
                <summary>Assets 1</summary>
                <include-fragment>Loading</include-fragment>
              </section>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a></h2>
                <summary>Assets 1</summary>
                <include-fragment>Loading</include-fragment>
              </section>
            </body></html>
            """,
            "html.parser",
        )

        def fake_fetch(url):
            tag = url.rsplit("/", 1)[-1]
            return Mock(
                status_code=200,
                text=f'<a href="/owner/repo/releases/download/{tag}/{tag}.zip">{tag}.zip</a>',
            )

        with patch("app.fetch_github", side_effect=fake_fetch) as fetch_github:
            app.append_missing_asset_fragments_for_release_list(soup, "owner", "repo")

        self.assertEqual(fetch_github.call_count, 2)
        sections = soup.find_all("section")
        self.assertTrue(app.page_has_asset_downloads(sections[0], "owner", "repo", "v2.0.0"))
        self.assertFalse(app.page_has_asset_downloads(sections[0], "owner", "repo", "v1.0.0"))
        self.assertTrue(app.page_has_asset_downloads(sections[1], "owner", "repo", "v1.0.0"))

    def test_append_missing_asset_fragments_ignores_release_note_headings_to_other_tags(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v2.0.0">v2.0.0</a></h2>
                <div class="markdown-body">
                  <h3><a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a></h3>
                </div>
                <summary>Assets 1</summary>
                <include-fragment>Loading</include-fragment>
              </section>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a></h2>
                <summary>Assets 1</summary>
                <include-fragment>Loading</include-fragment>
              </section>
            </body></html>
            """,
            "html.parser",
        )

        def fake_fetch(url):
            tag = url.rsplit("/", 1)[-1]
            return Mock(
                status_code=200,
                text=f'<a href="/owner/repo/releases/download/{tag}/{tag}.zip">{tag}.zip</a>',
            )

        with patch("app.fetch_github", side_effect=fake_fetch) as fetch_github:
            app.append_missing_asset_fragments_for_release_list(soup, "owner", "repo")

        self.assertEqual(fetch_github.call_count, 2)
        sections = soup.find_all("section")
        self.assertTrue(app.page_has_asset_downloads(sections[0], "owner", "repo", "v2.0.0"))
        self.assertFalse(app.page_has_asset_downloads(sections[0], "owner", "repo", "v1.0.0"))
        self.assertTrue(app.page_has_asset_downloads(sections[1], "owner", "repo", "v1.0.0"))

    def test_append_missing_asset_fragments_accepts_non_heading_release_titles(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="release-entry">
                <div class="f1 flex-auto min-width-0 text-normal">
                  <a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a>
                </div>
                <summary>Assets 1</summary>
                <include-fragment>Loading</include-fragment>
              </section>
            </body></html>
            """,
            "html.parser",
        )

        with patch(
            "app.fetch_github",
            return_value=Mock(
                status_code=200,
                text='<a href="/owner/repo/releases/download/v1.0.0/new.zip">new.zip</a>',
            ),
        ) as fetch_github:
            app.append_missing_asset_fragments_for_release_list(soup, "owner", "repo")

        fetch_github.assert_called_once_with("https://github.com/owner/repo/releases/expanded_assets/v1.0.0")
        self.assertTrue(app.page_has_asset_downloads(soup, "owner", "repo", "v1.0.0"))

    def test_append_missing_asset_fragments_detects_split_asset_count_label(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="release-entry">
                <h2><a href="/owner/repo/releases/tag/v1.0.0">v1.0.0</a></h2>
                <summary>Assets <span>2</span></summary>
                <include-fragment>Loading</include-fragment>
              </section>
            </body></html>
            """,
            "html.parser",
        )

        with patch(
            "app.fetch_github",
            return_value=Mock(
                status_code=200,
                text='<a href="/owner/repo/releases/download/v1.0.0/new.zip">new.zip</a>',
            ),
        ) as fetch_github:
            app.append_missing_asset_fragments_for_release_list(soup, "owner", "repo")

        fetch_github.assert_called_once_with("https://github.com/owner/repo/releases/expanded_assets/v1.0.0")
        self.assertTrue(app.page_has_asset_downloads(soup, "owner", "repo", "v1.0.0"))


if __name__ == "__main__":
    unittest.main()
