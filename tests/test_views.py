import numpy as np
import pytest

def test_spectrogram_survives_reset(qapp):
    from pyhantek.processing import spectrum
    from pyhantek.ui.extra_views import SpectrogramView

    v = SpectrogramView()
    sr = 1e6
    t = np.arange(20000) / sr
    f, db = spectrum(np.sin(2 * np.pi * 1000 * t), sr)
    v.add(f[1:], db[1:], sr / 2, "x", "#fff")
    v.reset_history()
    assert v.img.getViewBox() is not None, "image was removed from the plot"
    v.add(f[1:], db[1:], sr / 2, "x", "#fff")
    assert (v.data.max(axis=1) > -120).sum() == 1


def test_phosphor_draws_inside_screen(qapp):
    import pyqtgraph as pg

    from pyhantek.ui.extra_views import Phosphor

    plot = pg.PlotItem()
    ph = Phosphor(plot, 200, 80)
    x = np.linspace(0, 10, 500)
    ph.add([(x, np.sin(x)), None, None, None], tau=1.0)
    assert ph.buf[0].sum() > 0 and ph.buf[1:].sum() == 0
    r = ph.item.mapRectToParent(ph.item.boundingRect())
    assert (r.left(), r.top(), r.width(), r.height()) == pytest.approx((0, -4, 10, 8))
