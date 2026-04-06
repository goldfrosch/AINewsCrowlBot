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
