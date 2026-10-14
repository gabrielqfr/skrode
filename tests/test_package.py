def test_the_package_imports():
    # Keeps `pytest -q` meaningful before S6 adds real modules: an empty run
    # exits 5, which verify.sh would report as a failure.
    import router

    assert router.__doc__
