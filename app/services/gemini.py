from typing import Union, Literal

from google import genai
from google.genai.types import (
    GenerateContentResponse,
    GenerateContentConfig,
    GenerateContentConfigDict,
    ContentListUnion,
    ContentListUnionDict,
    HarmCategory,
    HarmBlockThreshold, SafetySetting,
)

from app.utils.config import settings

system_instructions = {
    "en": "You receive a json post from Telegram, and your task is to analyze it, tell us what you think about it, and maybe see some strangeness in it. If necessary, adapt the content for a Russian-speaking user so that they understand everything. In general, your task is to make the post short and clear, adapt it to cultural peculiarities, and if the post is in another language, you can suggest that the user google something to understand the topic better. You don't need to describe the channel once again, just speak to the essence of the post. You also don't need to mention that you work with JSON. Also, if there are media, photos, videos, voice clips, stickers attached to the post, please analyze and study them as well.",
    "de": "Sie erhalten einen json-Post von Telegram, und Ihre Aufgabe ist es, ihn zu analysieren, uns zu sagen, was Sie davon halten, und vielleicht einige Merkwürdigkeiten darin zu entdecken. Wenn nötig, passen Sie den Inhalt für russischsprachige Nutzer an, damit sie alles verstehen. Im Allgemeinen besteht Ihre Aufgabe darin, den Beitrag kurz und klar zu gestalten, ihn an kulturelle Besonderheiten anzupassen, und wenn der Beitrag in einer anderen Sprache verfasst ist, können Sie dem Nutzer vorschlagen, etwas zu googeln, um das Thema besser zu verstehen. Sie brauchen den Kanal nicht noch einmal zu beschreiben, sondern nur das Wesentliche des Beitrags anzusprechen. Sie müssen auch nicht erwähnen, dass Sie mit JSON arbeiten. Wenn dem Beitrag Medien, Fotos, Videos, Stimmen oder Sticker beigefügt sind, sollten Sie auch diese analysieren und untersuchen.",
    "ru": "Ты получаешь на вход json-пост из Telegram, и твоя задача - проанализировать его, рассказать, что ты о нем думаешь, возможно, увидеть в нем какие-то странности. Если нужно адаптируйте контент под русскоязычного пользователя, чтобы он все понял. В общем твоя задача изложить пост коротко и понятно, адаптировать под культурные особенности, если пост на другом языке, возможно предложить погуглить что-то пользователю, чтобы понять лучше тему. Не нужно лишний раз описывать канал, только по сути поста говори. Так же не нужно упоминать, что ты работаешь с JSON. Так же если к посту прикреплены медиа, фото, видео, голосовые, стикеры - пожалуйста их тоже анализируй и изучай.",
    "uk": "Ти отримуєш на вхід json-пост із Telegram, і твоє завдання - проаналізувати його, розповісти, що ти про нього думаєш, можливо, побачити в ньому якісь дивацтва. Якщо потрібно адаптуйте контент під російськомовного користувача, щоб він усе зрозумів. Загалом твоє завдання викласти пост коротко і зрозуміло, адаптувати під культурні особливості, якщо пост іншою мовою, можливо запропонувати погуглити щось користувачеві, щоб зрозуміти краще тему. Не потрібно зайвий раз описувати канал, тільки по суті поста говори. Так само не потрібно згадувати, що ти працюєш з JSON. Так само якщо до посту прикріплені медіа, фото, відео, голосові, стікери - будь ласка їх теж аналізуй і вивчай."
}


class GeminiService:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_GEMINI_API_KEY)
        self.model = "gemini-2.0-flash"

        self.safety_settings = [
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
            SafetySetting(
                category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

    async def generate_content(
        self,
        *,
        lang: Literal["en", "de", "ru", "uk"] = "en",
        model: str = None,
        contents: Union[ContentListUnion, ContentListUnionDict],
        config: Union[GenerateContentConfig, GenerateContentConfigDict, None] = None
    ) -> GenerateContentResponse:
        generate_content_config = GenerateContentConfig(
            temperature=0.8,
            response_mime_type="text/plain",
            safety_settings=self.safety_settings,
            system_instruction=system_instructions.get(lang)
        )

        return await self.client.aio.models.generate_content(
            contents=contents,
            model=model if model else self.model,
            config=config if config else generate_content_config,
        )


gemini_service = GeminiService()
