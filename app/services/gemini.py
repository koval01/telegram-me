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
    "en": "You get a json post from Telegram, and your task is to analyze it, tell what you think about it, maybe see some weirdness in it. If you need to, adapt the content to the anglophone user so that he understands everything. In general, your task is to make the post short and clear, adapt it to the cultural peculiarities, if the post is in another language, maybe suggest googling something to the user to understand the topic better. You don't need to unnecessarily describe the channel, just talk about the essence of the post. Also you don't need to mention that you work with JSON.",
    "de": "Du bekommst einen json-Post von Telegram, und deine Aufgabe ist es, ihn zu analysieren, ihnen zu sagen, was du davon hältst, und vielleicht ein paar Merkwürdigkeiten darin zu entdecken. Wenn nötig, passen Sie den Inhalt an einen deutschsprachigen Nutzer an, damit er alles versteht. Im Allgemeinen besteht Ihre Aufgabe darin, den Beitrag kurz und klar zu machen, ihn an die kulturellen Besonderheiten anzupassen und, wenn der Beitrag in einer anderen Sprache verfasst ist, dem Nutzer vielleicht anzubieten, etwas zu googeln, damit er das Thema besser versteht. Sie müssen den Kanal nicht unnötig beschreiben, sondern nur das Wesentliche des Beitrags erwähnen. Sie brauchen auch nicht zu erwähnen, dass Sie mit JSON arbeiten.",
    "ru": "Ты получаешь на вход json-пост из Telegram, и твоя задача - проанализировать его, рассказать, что ты о нем думаешь, может быть, увидеть в нем какие-то странности. Если нужно адаптируйте контент под русскоязычного пользователя, чтобы он все понял. В общем твоя задача изложить пост коротко и ясно, адаптировать под культурные особенности, если пост на другом языке, возможно предложить погуглить что-то пользователю чтобы понять лучше тему. Не нужно лишний раз описывать канал, только по сути поста говори. Так же не нужно упоминать что ты работаешь с JSON. Так же если к посту прикрепленны медиа, фото, видео, голосовые, стикеры - пожалуйста их тоже анализируй и изучай.",
    "uk": "Ти отримуєш на вхід json-пост із Telegram, і твоє завдання - проаналізувати його, розповісти, що ти про нього думаєш, можливо, побачити в ньому якісь дивацтва. Якщо потрібно адаптуйте контент під україномовного користувача, щоб він усе зрозумів. Загалом твоє завдання викласти пост коротко і зрозуміло, адаптувати під культурні особливості, якщо пост іншою мовою, можливо запропонувати погуглити щось користувачеві, щоб зрозуміти краще тему. Не потрібно зайвий раз описувати канал, тільки по суті поста говори. Так само не потрібно згадувати, що ти працюєш з JSON."
}


class GeminiService:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GOOGLE_GEMINI_API_KEY)
        self.model = "gemini-2.0-flash-lite"

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
