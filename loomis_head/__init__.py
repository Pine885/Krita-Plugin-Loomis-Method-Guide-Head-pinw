try:
    from krita import Krita
except ImportError:  # allows importing the geometry modules in tests
    Krita = None

if Krita is not None:
    from .loomis_head_plugin import LoomisHeadPlugin

    app = Krita.instance()
    extension = LoomisHeadPlugin(app)
    app.addExtension(extension)
