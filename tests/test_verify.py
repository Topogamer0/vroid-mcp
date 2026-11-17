from PIL import Image, ImageDraw, ImageFont

from vroid_mcp.verify import read_number, values_match, wait_until_stable


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def frames(*colors):
    seq = [Image.new("RGB", (40, 30), c) for c in colors]
    it = iter(seq)
    last = [seq[-1]]

    def grab():
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]
    return grab


def test_stable_after_change():
    clk = FakeClock()
    ref = Image.new("RGB", (40, 30), "white")
    r = wait_until_stable(frames("white", "white", "gray", "black", "black", "black"),
                          reference=ref, expect_change=True, clock=clk, sleep=clk.sleep)
    assert r.stable and r.changed and r.image.getpixel((0, 0)) == (0, 0, 0)


def test_no_change_times_out_when_change_expected():
    clk = FakeClock()
    ref = Image.new("RGB", (40, 30), "white")
    r = wait_until_stable(frames("white"), reference=ref, expect_change=True,
                          timeout=1.0, clock=clk, sleep=clk.sleep)
    assert not r.stable and not r.changed


def test_masked_flicker_counts_as_stable():
    clk = FakeClock()

    def grab():
        img = Image.new("RGB", (40, 30), "white")
        if int(clk.t * 10) % 2:
            ImageDraw.Draw(img).rectangle((0, 0, 19, 29), fill="black")  # "viewport"
        return img
    r = wait_until_stable(grab, masks=[(0, 0, 20, 30)], clock=clk, sleep=clk.sleep)
    assert r.stable and r.waited < 1


def test_values_match():
    assert values_match(0.35, 0.350)
    assert values_match(0.3504, 0.350)
    assert not values_match(0.36, 0.35)
    assert values_match(161.2, 161.2, decimals=1)
    assert not values_match(1.0, None)


def test_read_number(need_ocr):
    img = Image.new("RGB", (200, 40), "white")
    ImageDraw.Draw(img).text((120, 12), "0.350", fill=(60, 60, 60),
                             font=ImageFont.truetype("segoeui.ttf", 12))
    value, raw = read_number(img, (110, 5, 190, 35))
    assert value == 0.35, raw
