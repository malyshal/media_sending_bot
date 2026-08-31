import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from app.joyreactor.client import JoyReactorClient
from app.joyreactor.extractor import JoyReactorExtractor
from httpx import Response, Request

@pytest.fixture
def mock_client():
    client = JoyReactorClient()
    client.client = AsyncMock()
    return client

@pytest.fixture
def extractor():
    return JoyReactorExtractor("https://joyreactor.cc")

@pytest.mark.asyncio
async def test_decode_global_id(mock_client):
    # Test global ID conversion
    assert mock_client._decode_global_id("UG9zdDo0NDU5NzE=") == "445971"
    assert mock_client._decode_global_id("445971") == "445971"
    assert mock_client._decode_global_id("InvalidID") == "InvalidID"

@pytest.mark.asyncio
async def test_extract_media_url_image(extractor):
    # Fixture for post 445971: contains ad images and the real post image
    html = """
    <html>
        <body>
            <img src="https://mc.yandex.ru/watch/98649933">
            <img src="/avatar/user123.jpg">
            <img src="https://img2.joyreactor.cc/pics/post/тюлень-интелегент-песочница-359574.jpeg">
            <img src="/service/icon.png">
        </body>
    </html>
    """
    url = await extractor.extract_media_url(html, "445971", is_video=False)
    assert url == "https://img2.joyreactor.cc/pics/post/тюлень-интелегент-песочница-359574.jpeg"
    assert "mc.yandex.ru" not in url

@pytest.mark.asyncio
async def test_extract_media_url_video(extractor):
    # Fixture for video post
    html = """
    <html>
        <body>
            <video>
                <source src="/images/videowithsound.mp4" type="video/mp4">
            </video>
            <img src="https://mc.yandex.ru/watch/123">
        </body>
    </html>
    """
    url = await extractor.extract_media_url(html, "445971", is_video=True)
    assert url == "https://joyreactor.cc/images/videowithsound.mp4"

@pytest.mark.asyncio
async def test_extract_media_url_not_found(extractor):
    html = "<html><body><img src='https://mc.yandex.ru/watch/123'></body></html>"
    url = await extractor.extract_media_url(html, "445971", is_video=False)
    assert url is None

@pytest.mark.asyncio
async def test_execute_graphql_success(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"test": "value"}}
    mock_client.client.post.return_value = mock_response

    result = await mock_client.execute("query { test }")
    assert result == {"test": "value"}

@pytest.mark.asyncio
async def test_execute_graphql_errors(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"errors": [{"message": "GraphQL error"}]}
    mock_client.client.post.return_value = mock_response

    with pytest.raises(Exception) as excinfo:
        await mock_client.execute("query { test }")
    assert "GraphQL errors" in str(excinfo.value)

@pytest.mark.asyncio
async def test_execute_http_403(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 403
    import httpx
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("403 Forbidden", request=Request("POST", "url"), response=mock_response)
    mock_client.client.post.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        await mock_client.execute("query { test }")

@pytest.mark.asyncio
async def test_normalize_media_type(mock_client):
    assert mock_client._normalize_media_type("image/jpeg") == "image"
    assert mock_client._normalize_media_type("video/mp4") == "video"
    assert mock_client._normalize_media_type("unknown/type") == "image"

@pytest.mark.asyncio
async def test_search_tags_parsing(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "tagAutocomplete": [
                {"id": "VGFnOjM0Njc=", "name": "test_tag", "count": 100, "nsfw": False, "unsafe": False}
            ]
        }
    }
    mock_client.client.post.return_value = mock_response

    tags = await mock_client.search_tags("test")
    assert len(tags) == 1
    assert tags[0].name == "test_tag"
    assert tags[0].id == "VGFnOjM0Njc="
