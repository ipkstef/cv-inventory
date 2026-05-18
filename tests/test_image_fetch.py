import io

import pytest
import respx
from httpx import Response
from PIL import Image

from scan_and_identify.image_fetch import FetchError, fetch_image


def _png_bytes(color=(123, 45, 67)) -> bytes:
    img = Image.new("RGB", (200, 200), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_fetch_image_returns_pil_image():
    url = "https://example.com/scan.png"
    with respx.mock(assert_all_called=True) as m:
        m.get(url).mock(return_value=Response(200, content=_png_bytes()))
        img = await fetch_image(url)
    assert img.size == (200, 200)
    assert img.mode == "RGB"


@pytest.mark.asyncio
async def test_fetch_image_raises_on_non_200():
    url = "https://example.com/nope.png"
    with respx.mock() as m:
        m.get(url).mock(return_value=Response(404))
        with pytest.raises(FetchError, match="404"):
            await fetch_image(url)


@pytest.mark.asyncio
async def test_fetch_image_raises_on_non_image_content():
    url = "https://example.com/text.png"
    with respx.mock() as m:
        m.get(url).mock(return_value=Response(200, content=b"not an image"))
        with pytest.raises(FetchError):
            await fetch_image(url)
