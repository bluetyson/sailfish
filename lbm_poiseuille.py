#!/usr/bin/python -u

import sys

import numpy
import lbm
import geo

import optparse
from optparse import OptionGroup, OptionParser, OptionValueError


class LBMGeoPoiseuille(geo.LBMGeo2D):
	"""2D Poiseuille geometry."""

	maxv = 0.02

	def _define_nodes(self):
		if self.options.horizontal:
			for i in range(0, self.lat_w):
				self.set_geo((i, 0), self.NODE_WALL)
				self.set_geo((i, self.lat_h-1), self.NODE_WALL)
		else:
			for i in range(0, self.lat_h):
				self.set_geo((0, i), self.NODE_WALL)
				self.set_geo((self.lat_w-1, i), self.NODE_WALL)

		if self.options.pressure:
			if self.options.horizontal:
				pressure = self.maxv * (8.0 * self.options.visc) / (self.get_chan_width()**2) * self.lat_w

				for i in range(1, self.lat_h-1):
					self.set_geo((0, i), self.NODE_PRESSURE, (1.0/3.0) - pressure/2.0)
					self.set_geo((self.lat_w-1, i), self.NODE_PRESSURE, (1.0/3.0) + pressure/2.0)
			else:
				pressure = self.maxv * (8.0 * self.options.visc) / (self.get_chan_width()**2) * self.lat_h

				for i in range(1, self.lat_w-1):
					self.set_geo((i, 0), self.NODE_PRESSURE, (1.0/3.0) + pressure/2.0)
					self.set_geo((i, self.lat_h-1), self.NODE_PRESSURE, (1.0/3.0) - pressure/2.0)

	def init_dist(self, dist):
		if self.options.static:
			if self.options.pressure:
				pressure = self.maxv * (8.0 * self.options.visc) / (self.get_chan_width()**2)

				if self.options.horizontal:
					for x in range(0, self.lat_w):
						self.velocity_to_dist((x, 0), (0.0, 0.0), dist, rho=(1.0 + 3.0 * pressure * (self.lat_w/2.0 - x)))
						self.fill_dist((x, 0), dist, (x, slice(None)))
				else:
					for y in range(0, self.lat_h):
						self.velocity_to_dist((0, y), (0.0, 0.0), dist, rho=(1.0 + 3.0 * pressure * (self.lat_h/2.0 - y)))
						self.fill_dist((0, y), dist, (slice(None), y))
			else:
				profile = self.get_velocity_profile()

				if self.options.horizontal:
					for y in range(0, self.lat_h):
						self.velocity_to_dist((0, y), (profile[y], 0.0), dist)
					self.fill_dist((0, slice(None)), dist)
				else:
					for x in range(0, self.lat_w):
						self.velocity_to_dist((x, 0), (0.0, profile[x]), dist)
					self.fill_dist((slice(None), 0), dist)
		else:
			self.velocity_to_dist((0, 0), (0.0, 0.0), dist)
			self.fill_dist((0, 0), dist)

	def get_velocity_profile(self):
		width = self.get_chan_width()
		lat_width = self.get_width()
		ret = []
		h = 0

		bc = self.get_bc()
		if bc.midgrid:
			h = -0.5

		for x in range(0, lat_width):
			tx = x+h
			ret.append(4.0*self.maxv/width**2 * tx * (width-tx))

		return ret

	def get_chan_width(self):
		width = self.get_width() - 1
		bc = self.get_bc()
		if bc.midgrid:
			return width - 1
		else:
			return width

	def get_width(self):
		if self.options.horizontal:
			return self.lat_h
		else:
			return self.lat_w

	def get_reynolds(self, viscosity):
		return int(self.get_width() * self.maxv/viscosity)

class LPoiSim(lbm.LBMSim):

	filename = 'poiseuille'

	def __init__(self, geo_class, args=sys.argv[1:], defaults=None):
		opts = []
		opts.append(optparse.make_option('--test', dest='test', action='store_true', default=False, help='generate test data'))
		opts.append(optparse.make_option('--horizontal', dest='horizontal', action='store_true', default=False, help='use horizontal channel'))
		opts.append(optparse.make_option('--static', dest='static', action='store_true', default=False, help='start with the correct velocity profile in the whole simulation domain'))
		opts.append(optparse.make_option('--pressure', dest='pressure', action='store_true', default=False, help='use a pressure gradient instead of a body force to drive the flow'))


		if defaults is not None:
			defaults_ = defaults
		else:
			defaults_ = {'max_iters': 500000, 'visc': 0.1, 'lat_w': 64, 'lat_h': 64}

		lbm.LBMSim.__init__(self, geo_class, misc_options=opts, args=args, defaults=defaults_)

		if self.options.test or self.options.benchmark:
			self._init_geo()

			if not self.options.pressure:
				self.options.periodic_y = not self.options.horizontal
				self.options.periodic_x = self.options.horizontal

				if self.options.horizontal:
					self.options.accel_x = geo_class.maxv * (8.0 * self.options.visc) / (self.geo.get_chan_width()**2)
					if self.options.test:
						self.add_iter_hook(self.options.max_iters-1, self.output_profile_horiz)
				else:
					self.options.accel_y = geo_class.maxv * (8.0 * self.options.visc) / (self.geo.get_chan_width()**2)
					if self.options.test:
						self.add_iter_hook(self.options.max_iters-1, self.output_profile_vert)

			if self.options.test:
				self.add_iter_hook(1000, self.output_pars, every=True)
			else:
				self.options.max_iters = 0

	def get_profile(self):
		if self.options.horizontal:
			return self.vx[:,int(self.options.lat_w/2)]
		else:
			return self.vy[int(self.options.lat_h/2),:]

	def output_pars(self):
		print numpy.max(self.geo.mask_array_by_fluid(self.vx)),	numpy.max(self.geo.mask_array_by_fluid(self.vy)) / 0.02, numpy.average(self.geo.mask_array_by_fluid(self.rho))

	def output_profile_vert(self):
		print '# Re = %d' % self.geo.get_reynolds(self.options.visc)

		for i, (x, y, z) in enumerate(zip(
				self.vx[int(self.options.lat_h/2),:],
				self.vy[int(self.options.lat_h/2),:],
				self.rho[int(self.options.lat_h/2),:],
				)):
			print i, x, y, z

	def output_profile_horiz(self):
		print '# Re = %d' % self.geo.get_reynolds(self.options.visc)

		for i, (x, y, z) in enumerate(zip(
				self.vx[:,int(self.options.lat_w/2)],
				self.vy[:,int(self.options.lat_w/2)],
				self.rho[:,int(self.options.lat_w/2)],
				)):
			print i, x, y, z

if __name__ == '__main__':
	sim = LPoiSim(LBMGeoPoiseuille)
	sim.run()
