# coding=utf-8
"""Optional image preprocessing, all steps opt-in per request. Geometry is mapped back to original pixels."""
from __future__ import absolute_import, division, print_function

import io

from PIL import Image, ImageFilter, ImageOps


class PreprocessSpec(object):
    """
    Optional, per-request image preprocessing steps.
    """

    def __init__(self, *args, **kwargs):
        """
        :param auto_rotate: bool
        :param grayscale: bool
        :param denoise: bool
        :param contrast: bool
        :param upscale: bool
        :param upscale_target: int - longest side after upscale
        :param region: list[int] | None - x, y, w, h in original pixels
        """
        args = list(args)
        self.__m_autoRotate = kwargs.pop('auto_rotate', args.pop(0) if args else False)
        self.__m_grayscale = kwargs.pop('grayscale', args.pop(0) if args else False)
        self.__m_denoise = kwargs.pop('denoise', args.pop(0) if args else False)
        self.__m_contrast = kwargs.pop('contrast', args.pop(0) if args else False)
        self.__m_upscale = kwargs.pop('upscale', args.pop(0) if args else False)
        self.__m_upscaleTarget = kwargs.pop('upscale_target', args.pop(0) if args else 2000)
        self.__m_region = kwargs.pop('region', args.pop(0) if args else None)

    def getAutoRotate(self):
        """
        :return: bool
        """
        return self.__m_autoRotate

    def setAutoRotate(self, autoRotate):
        """
        :param autoRotate: bool
        """
        self.__m_autoRotate = autoRotate

    def getGrayscale(self):
        """
        :return: bool
        """
        return self.__m_grayscale

    def setGrayscale(self, grayscale):
        """
        :param grayscale: bool
        """
        self.__m_grayscale = grayscale

    def getDenoise(self):
        """
        :return: bool
        """
        return self.__m_denoise

    def setDenoise(self, denoise):
        """
        :param denoise: bool
        """
        self.__m_denoise = denoise

    def getContrast(self):
        """
        :return: bool
        """
        return self.__m_contrast

    def setContrast(self, contrast):
        """
        :param contrast: bool
        """
        self.__m_contrast = contrast

    def getUpscale(self):
        """
        :return: bool
        """
        return self.__m_upscale

    def setUpscale(self, upscale):
        """
        :param upscale: bool
        """
        self.__m_upscale = upscale

    def getUpscaleTarget(self):
        """
        :return: int
        """
        return self.__m_upscaleTarget

    def setUpscaleTarget(self, upscaleTarget):
        """
        :param upscaleTarget: int
        """
        self.__m_upscaleTarget = upscaleTarget

    def getRegion(self):
        """
        :return: list[int] | None
        """
        return self.__m_region

    def setRegion(self, region):
        """
        :param region: list[int] | None
        """
        self.__m_region = region

    def toDict(self):
        """
        :return: dict
        """
        return {
            'auto_rotate': self.__m_autoRotate,
            'grayscale': self.__m_grayscale,
            'denoise': self.__m_denoise,
            'contrast': self.__m_contrast,
            'upscale': self.__m_upscale,
            'upscale_target': self.__m_upscaleTarget,
            'region': self.__m_region,
        }

    def __repr__(self):
        return 'PreprocessSpec({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    @classmethod
    def fromDict(cls, d):
        """
        :param d: dict | None
        :return: PreprocessSpec
        """
        d = d or {}
        return cls(
            auto_rotate=bool(d.get('auto_rotate', False)),
            grayscale=bool(d.get('grayscale', False)),
            denoise=bool(d.get('denoise', False)),
            contrast=bool(d.get('contrast', False)),
            upscale=bool(d.get('upscale', False)),
            upscale_target=int(d.get('upscale_target', 2000)),
            region=list(d['region']) if d.get('region') else None,
        )

    def isActive(self):
        """
        :return: bool - any step enabled
        """
        return any(
            (
                self.__m_autoRotate,
                self.__m_grayscale,
                self.__m_denoise,
                self.__m_contrast,
                self.__m_upscale,
                self.__m_region,
            )
        )

    active = property(fget=isActive)

    auto_rotate = property(fget=getAutoRotate, fset=setAutoRotate)
    grayscale = property(fget=getGrayscale, fset=setGrayscale)
    denoise = property(fget=getDenoise, fset=setDenoise)
    contrast = property(fget=getContrast, fset=setContrast)
    upscale = property(fget=getUpscale, fset=setUpscale)
    upscale_target = property(fget=getUpscaleTarget, fset=setUpscaleTarget)
    region = property(fget=getRegion, fset=setRegion)


class Transform(object):
    """
    Maps processed-image coordinates back to the original image.
    """

    def __init__(self, *args, **kwargs):
        """
        :param scale: float
        :param offset_x: float
        :param offset_y: float
        :param steps: list[str] - applied step names
        """
        args = list(args)
        self.__m_scale = kwargs.pop('scale', args.pop(0) if args else 1.0)
        self.__m_offsetX = kwargs.pop('offset_x', args.pop(0) if args else 0.0)
        self.__m_offsetY = kwargs.pop('offset_y', args.pop(0) if args else 0.0)
        self.__m_steps = kwargs.pop('steps', args.pop(0) if args else list())

    def getScale(self):
        """
        :return: float
        """
        return self.__m_scale

    def setScale(self, scale):
        """
        :param scale: float
        """
        self.__m_scale = scale

    def getOffsetX(self):
        """
        :return: float
        """
        return self.__m_offsetX

    def setOffsetX(self, offsetX):
        """
        :param offsetX: float
        """
        self.__m_offsetX = offsetX

    def getOffsetY(self):
        """
        :return: float
        """
        return self.__m_offsetY

    def setOffsetY(self, offsetY):
        """
        :param offsetY: float
        """
        self.__m_offsetY = offsetY

    def getSteps(self):
        """
        :return: list[str]
        """
        return self.__m_steps

    def setSteps(self, steps):
        """
        :param steps: list[str]
        """
        self.__m_steps = steps

    def toDict(self):
        """
        :return: dict
        """
        return {
            'scale': self.__m_scale,
            'offset_x': self.__m_offsetX,
            'offset_y': self.__m_offsetY,
            'steps': self.__m_steps,
        }

    def __repr__(self):
        return 'Transform({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    def restore(self, result):
        """
        :param result: dict - unified OCR result in processed-image pixels
        :return: dict - the same result in original-image pixels
        """
        if self.__m_scale == 1.0 and not self.__m_offsetX and not self.__m_offsetY:
            return result
        for line in result.get('TextOverlay', {}).get('Lines', []):
            for w in line.get('Words', []):
                w['Left'] = w['Left'] / self.__m_scale + self.__m_offsetX
                w['Top'] = w['Top'] / self.__m_scale + self.__m_offsetY
                w['Width'] = w['Width'] / self.__m_scale
                w['Height'] = w['Height'] / self.__m_scale
            if line.get('Words'):
                line['MinTop'] = min(w['Top'] for w in line['Words'])
                line['MaxHeight'] = max(w['Height'] for w in line['Words'])
        return result

    scale = property(fget=getScale, fset=setScale)
    offset_x = property(fget=getOffsetX, fset=setOffsetX)
    offset_y = property(fget=getOffsetY, fset=setOffsetY)
    steps = property(fget=getSteps, fset=setSteps)


def apply(data, spec):
    if not spec.active:
        return data, Transform()
    im = Image.open(io.BytesIO(data))
    im.load()
    t = Transform()
    if spec.auto_rotate:
        im = ImageOps.exif_transpose(im) or im
        t.steps.append('exif_transpose')
    if spec.region:
        x, y, w, h = spec.region
        x, y = max(0, x), max(0, y)
        im = im.crop((x, y, min(im.width, x + w), min(im.height, y + h)))
        t.offset_x, t.offset_y = float(x), float(y)
        t.steps.append('crop')
    if spec.grayscale:
        im = ImageOps.grayscale(im)
        t.steps.append('grayscale')
    if spec.denoise:
        im = im.filter(ImageFilter.MedianFilter(3))
        t.steps.append('denoise')
    if spec.contrast:
        im = ImageOps.autocontrast(im, cutoff=1)
        t.steps.append('autocontrast')
    if spec.upscale:
        longest = max(im.size)
        if longest and longest < spec.upscale_target:
            factor = min(3, max(1, spec.upscale_target // longest))
            if factor > 1:
                im = im.resize((im.width * factor, im.height * factor), Image.LANCZOS)
                t.scale = float(factor)
                t.steps.append('upscale x{}'.format(factor))
    buf = io.BytesIO()
    im.convert('RGB').save(buf, format='PNG')
    return buf.getvalue(), t
