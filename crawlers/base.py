from dataclasses import dataclass, field


@dataclass
class Article:
    url: str
    title: str
    source: str
    description: str = ""
    author: str = ""
    image_url: str = ""
    published_at: str = ""
    platform_score: float = 0.0
    keywords: list = field(default_factory=list)
    # 어떤 주제군(필라)에서 수집됐는지. 필라마다 신선도 컷이 달라 하류 단계가 이 값을 본다.
    pillar: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "description": self.description[:500],
            "author": self.author[:100],
            "image_url": self.image_url,
            "published_at": self.published_at,
            "platform_score": self.platform_score,
            "keywords": self.keywords,
        }
