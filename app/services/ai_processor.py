import io
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Union, Literal, Optional, Any, Tuple

import aiohttp
from PIL import Image
from PIL.ImageFile import ImageFile
from google import genai
from google.genai.types import Part

from app.services.gemini import GeminiService
from app.utils.json import clean_json_of_post
from app.utils.mime import MIMEExtractor

logger = logging.getLogger(__name__)


class GenerateResponse:
    """Handles generation of AI responses for social media posts with media attachments."""

    def __init__(
            self,
            post: dict,
            gemini_service: GeminiService,
            lang: Literal["en", "de", "ru", "uk"] = "en"
    ) -> None:
        """
        Initialize the response generator.

        Args:
            post: Original post data
            gemini_service: Service for interacting with Gemini AI
            lang: Language code for the response
        """
        self.prompt = clean_json_of_post(post)
        self.data = clean_json_of_post(post, media_meta=True)
        self.gemini_service = gemini_service
        self.lang = lang

    @staticmethod
    def _process_dict_for_media_urls(data: Dict[str, Any]) -> Tuple[List[str], Dict[str, Any]]:
        """
        Process a dictionary to extract media URLs and remove media attachments.

        Args:
            data: Dictionary to process

        Returns:
            Tuple of (extracted URLs, modified dictionary)
        """
        urls = []
        if 'media' in data and isinstance(data['media'], list):
            urls.extend(
                media_item['url'] for media_item in data['media']
                if isinstance(media_item, dict) and 'url' in media_item
            )
            del data['media']
        return urls, data

    def _process_item_for_media_urls(self, item: Union[Dict, List]) -> List[str]:
        """
        Recursively process an item (dict or list) to extract media URLs.

        Args:
            item: Item to process

        Returns:
            List of extracted URLs
        """
        urls = []
        if isinstance(item, dict):
            dict_urls, _ = self._process_dict_for_media_urls(item)
            urls.extend(dict_urls)
            urls.extend(self._process_item_for_media_urls(list(item.values())))
        elif isinstance(item, list):
            for subitem in item:
                urls.extend(self._process_item_for_media_urls(subitem))
        return urls

    def extract_and_remove_media_urls(self) -> List[str]:
        """
        Recursively retrieves all URLs from media attachments in the data structure,
        removes the media attachments, and returns a list of URLs.
        Modifies self.data in place by removing all media attachments.

        Returns:
            List of extracted URLs
        """
        return self._process_item_for_media_urls(self.data)

    @staticmethod
    async def _download_image(session: aiohttp.ClientSession, url: str) -> ImageFile:
        """
        Download and open an image from a URL.

        Args:
            session: Active aiohttp session
            url: Image URL to download

        Returns:
            PIL Image object

        Raises:
            ValueError: If download fails
        """
        async with session.get(url) as response:
            if response.status != 200:
                raise ValueError(f"Failed to download image. Status code: {response.status}")
            data = await response.read()
            return Image.open(io.BytesIO(data))

    @staticmethod
    async def _download_audio(session: aiohttp.ClientSession, url: str, mime: str) -> Part:
        """
        Download audio data and create a Gemini Blob.

        Args:
            session: Active aiohttp session
            url: Audio URL to download
            mime: MIME type of the audio

        Returns:
            Gemini Blob object

        Raises:
            ValueError: If download fails
        """
        async with session.get(url) as response:
            if response.status != 200:
                raise ValueError(f"Failed to download audio. Status code: {response.status}")
            data = await response.read()
            return genai.types.Part.from_bytes(data=data, mime_type=mime)

    async def _process_media_item(
            self,
            session: aiohttp.ClientSession,
            url: str,
            mime: str
    ) -> Optional[Union[ImageFile, Part]]:
        """
        Process a single media item based on its MIME type.

        Args:
            session: Active aiohttp session
            url: Media URL
            mime: MIME type

        Returns:
            Media object or None if type not supported
        """
        try:
            if mime in {"image/jpeg", "image/png"}:
                return await self._download_image(session, url)
            elif mime == "audio/ogg":
                return await self._download_audio(session, url, mime)
            logger.warning(f"Unsupported media type: {mime}")
            return None
        except Exception as e:
            logger.error(f"Error processing media {url}: {str(e)}")
            return None

    async def download_media(self) -> Optional[List[Union[ImageFile, Part]]]:
        """
        Download and process all media attachments from the post.

        Returns:
            List of media objects or None if no media found
        """
        extractor = MIMEExtractor()
        results = await extractor.extract(self.extract_and_remove_media_urls())
        if not results:
            return None

        media = []
        async with aiohttp.ClientSession() as session:
            for url, mime in results.items():
                media_item = await self._process_media_item(session, url, mime)
                if media_item:
                    media.append(media_item)

        return media if media else None

    def _generate_content_payload(self, media: Optional[List[Union[ImageFile, Part]]]) -> List[str | list[ImageFile | Part]]:
        """
        Generate the content payload for the AI request.

        Args:
            media: List of media objects

        Returns:
            Content payload list
        """
        dt = datetime.now(timezone.utc)
        formatted_time = dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
        content = []
        content.extend(f"{json.dumps(self.prompt)}\n{formatted_time}")
        if media:
            content.append(media)
        return content

    async def generate(self) -> Dict[str, Any]:
        """
        Generate the AI response for the post.

        Returns:
            Dictionary containing AI response and metadata

        Raises:
            RuntimeError: If content generation fails
        """
        try:
            media = await self.download_media()
            content = self._generate_content_payload(media)

            response = await self.gemini_service.generate_content(
                contents=content,
                lang=self.lang
            )

            return {
                "ai": {"text": response.text},
                "content": self.prompt,
                "serverTime": datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
            }
        except Exception as e:
            logger.error(f"Failed to generate response: {str(e)}")
            raise RuntimeError("Failed to generate AI response") from e

    def __str__(self) -> str:
        """Return string representation of the prompt."""
        return json.dumps(self.prompt)
