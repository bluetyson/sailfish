#!/usr/bin/python

import numpy
import lbm

from sim import *

import optparse
from optparse import OptionGroup, OptionParser, OptionValueError


class LBMGeoLDC(lbm.LBMGeo):
	"""Lid-driven cavity geometry."""

	max_v = 0.1

	def reset(self):
		"""Initialize the simulation for the lid-driven cavity geometry."""
		self.map = numpy.zeros((self.lat_h, self.lat_w), numpy.int32)
		# bottom/top
		for i in range(0, self.lat_w):
			self.map[0][i] = numpy.int32(GEO_WALL)
			self.map[self.lat_h-1][i] = numpy.int32(GEO_INFLOW)
		# left/right
		for i in range(0, self.lat_h):
			self.map[i][0] = self.map[i][self.lat_w-1] = numpy.int32(GEO_WALL)

		self.update_map()

	def init_dist(self, dist):
		for x in range(0, self.lat_w):
			for y in range(0, self.lat_h):
				dist[0][y][x] = numpy.float32(4.0/9.0)
				dist[1][y][x] = dist[2][y][x] = dist[3][y][x] = dist[4][y][x] = numpy.float32(1.0/9.0)
				dist[5][y][x] = dist[6][y][x] = dist[7][y][x] = dist[8][y][x] = numpy.float32(1.0/36.0)

		for i in range(0, self.lat_w):
			self.velocity_to_dist(LBMGeoLDC.max_v, 0.0, dist, i, self.lat_h-1)

	def get_reynolds(self, viscosity):
		return int((self.lat_w-1) * LBMGeoLDC.max_v/viscosity)

class LDCSim(lbm.LBMSim):

	def __init__(self, geo_class):
		opts = []
		opts.append(optparse.make_option('--test_re100', dest='test_re100', action='store_true', default=False, help='generate test data for Re=100'))
		opts.append(optparse.make_option('--test_re1000', dest='test_re1000', action='store_true', default=False, help='generate test data for Re=1000'))

		lbm.LBMSim.__init__(self, geo_class, misc_options=opts)

		if self.options.test_re100:
			self.options.batch = True
			self.options.max_iters = 50000
			self.options.lat_w = 128
			self.options.lat_h = 128
			self.options.visc = 0.127
		elif self.options.test_re1000:
			self.options.batch = True
			self.options.max_iters = 50000
			self.options.lat_w = 128
			self.options.lat_h = 128
			self.options.visc = 0.0127


		self.add_iter_hook(49999, self.output_profile)

	def output_profile(self):
		print '# Re = %d' % self.geo.get_reynolds(self.options.visc)

		for i, (x, y) in enumerate(zip(self.vx[:,int(self.options.lat_w/2)] / self.geo_class.max_v,
										self.vy[int(self.options.lat_h/2),:] / self.geo_class.max_v)):
			print float(i) / self.options.lat_h, x, y


sim = LDCSim(LBMGeoLDC)
sim.run()
