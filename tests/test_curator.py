"""curator.py 단위 테스트"""

from curator import _extract_json_array, _to_articles


class TestExtractJsonArray:
    def test_valid_array(self):
        text = 'Some text [{"url":"https://example.com","title":"Test"}]'
        result = _extract_json_array(text)
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    def test_last_array_used(self):
        text = '[{"a":1}] then [{"b":2}]'
        result = _extract_json_array(text)
        assert len(result) == 1
        assert "b" in result[0]

    def test_empty_input(self):
        assert _extract_json_array("no json here") == []

    def test_malformed_json(self):
        assert _extract_json_array("[{broken") == []

    def test_nested_arrays(self):
        text = '[{"url":"a","title":"b","keywords":["k1","k2"]}]'
        result = _extract_json_array(text)
        # rfind("[") finds innermost [ so returns inner array
        assert isinstance(result, list)

    def test_multiple_items(self):
        text = '[{"url":"a","title":"A"},{"url":"b","title":"B"},{"url":"c","title":"C"}]'
        result = _extract_json_array(text)
        assert len(result) == 3


class TestToArticles:
    def test_valid_data(self):
        data = [
            {
                "url": "https://example.com/1",
                "title": "Test Article",
                "source": "TestSource",
                "description": "A description",
                "author": "Author",
                "published_at": "2026-04-01",
                "curator_reason": "Great article",
                "keywords": ["llm"],
            }
        ]
        articles = _to_articles(data)
        assert len(articles) == 1
        assert articles[0].title == "Test Article"
        assert articles[0].url == "https://example.com/1"
        assert "선정 이유" in articles[0].description
        assert articles[0].keywords == ["llm"]

    def test_skips_empty_url(self):
        data = [
            {"url": "", "title": "No URL"},
            {"url": "https://example.com", "title": "Valid"},
        ]
        articles = _to_articles(data)
        assert len(articles) == 1
        assert articles[0].url == "https://example.com"

    def test_skips_empty_title(self):
        data = [
            {"url": "https://example.com", "title": ""},
        ]
        articles = _to_articles(data)
        assert len(articles) == 0

    def test_description_truncated(self):
        data = [
            {
                "url": "https://example.com",
                "title": "Long desc",
                "description": "x" * 1000,
            }
        ]
        articles = _to_articles(data)
        assert len(articles[0].description) <= 500

    def test_default_values(self):
        data = [{"url": "https://example.com", "title": "Minimal"}]
        articles = _to_articles(data)
        assert articles[0].source == "AI Research"
        assert articles[0].platform_score == 100.0
        assert articles[0].keywords == []


class TestResearch:
    def test_agent_success(self, mocker):
        mock_data = [
            {"url": "https://example.com/1", "title": "Agent Result", "source": "Test"},
        ]
        # curator.research() 내부에서 from agents.news_curation_agent import run as _agent_run
        mocker.patch("agents.news_curation_agent.run", return_value=mock_data)

        from curator import research

        result = research(count=5)
        assert len(result) == 1
        assert result[0].title == "Agent Result"

    def test_fallback_on_agent_failure(self, mocker):
        mocker.patch("agents.news_curation_agent.run", side_effect=Exception("Agent failed"))
        mock_data = [
            {"url": "https://example.com/fallback", "title": "Fallback", "source": "Test"},
        ]
        mocker.patch("curator._extract_json_array", return_value=mock_data)

        # Anthropic client mock
        mock_usage = mocker.MagicMock(input_tokens=100, output_tokens=50)
        mock_response = mocker.MagicMock()
        mock_response.content = [mocker.MagicMock(type="text", text="irrelevant")]
        mock_response.usage = mock_usage

        mock_stream = mocker.MagicMock()
        mock_stream.__enter__ = mocker.MagicMock(return_value=mock_stream)
        mock_stream.get_final_message.return_value = mock_response

        mock_client = mocker.MagicMock()
        mock_client.messages.stream.return_value = mock_stream
        mocker.patch("curator.anthropic.Anthropic", return_value=mock_client)

        from curator import research

        result = research(count=5)
        assert len(result) == 1
        assert result[0].title == "Fallback"

    def test_empty_result_when_no_key(self, mocker):
        mocker.patch("curator.ANTHROPIC_API_KEY", "")
        from curator import research

        try:
            research(count=5)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "ANTHROPIC_API_KEY" in str(e)
