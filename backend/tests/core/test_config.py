"""Settings parsing.

Written after a real failure: starting the app with a perfectly ordinary
`MEDREPORT_CORS_ORIGINS=http://localhost:3000` crashed with a JSONDecodeError
whose message never mentioned JSON.
"""

import pytest

from app.core.config import Environment, Settings

SECRET = "y" * 48


class TestCorsOrigins:
    def test_a_comma_separated_list_parses(self) -> None:
        # What anyone would actually write in an env file. pydantic-settings
        # JSON-decodes complex fields inside the source, so without NoDecode this
        # raises before any validator runs.
        settings = Settings(cors_origins="http://localhost:3000,https://app.example.com")  # type: ignore[arg-type]

        assert settings.cors_origins == ["http://localhost:3000", "https://app.example.com"]

    def test_a_single_origin_parses(self) -> None:
        settings = Settings(cors_origins="http://localhost:3000")  # type: ignore[arg-type]

        assert settings.cors_origins == ["http://localhost:3000"]

    def test_whitespace_is_trimmed(self) -> None:
        settings = Settings(cors_origins=" http://a.com , http://b.com ")  # type: ignore[arg-type]

        assert settings.cors_origins == ["http://a.com", "http://b.com"]

    def test_json_still_works(self) -> None:
        # Kept working so an existing deployment using the documented JSON form
        # does not break on upgrade.
        settings = Settings(cors_origins='["http://a.com"]')  # type: ignore[arg-type]

        assert settings.cors_origins == ["http://a.com"]

    def test_empty_means_no_origins(self) -> None:
        assert Settings(cors_origins="").cors_origins == []  # type: ignore[arg-type]

    def test_a_real_list_is_untouched(self) -> None:
        assert Settings(cors_origins=["http://a.com"]).cors_origins == ["http://a.com"]


class TestSecrets:
    def test_a_deployed_environment_refuses_the_dev_secret(self) -> None:
        with pytest.raises(ValueError, match="MEDREPORT_JWT_SECRET"):
            Settings(environment=Environment.PRODUCTION)

    def test_a_real_secret_is_accepted(self) -> None:
        assert Settings(environment=Environment.PRODUCTION, jwt_secret=SECRET).is_production

    def test_local_development_needs_no_secret(self) -> None:
        assert Settings(environment=Environment.LOCAL).is_production is False
