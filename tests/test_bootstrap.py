import waloader
import waloader_sdk


def test_packages_importable() -> None:
    assert waloader.__version__ == "0.1.0"
    assert waloader_sdk.__version__ == "0.1.0"
