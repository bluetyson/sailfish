#!/usr/bin/python

import pycuda.autoinit
import pycuda.driver as cuda
import math
import numpy
import sys
import tables
import time

import vis2d
import geo2d

import optparse
from optparse import OptionGroup, OptionParser, OptionValueError

from mako.template import Template

def _convert_to_double(src):
	import re
	return re.sub('([0-9]+\.[0-9]*)f', '\\1', src.replace('float', 'double'))


class Values(optparse.Values):
	def __init__(self, *args):
		optparse.Values.__init__(self, *args)
		self.specified = set()

	def __setattr__(self, name, value):
		self.__dict__[name] = value
		if hasattr(self, 'specified'):
			self.specified.add(name)


class LBMSim(object):

	filename = 'lbm_sim'

	def __init__(self, geo_class, misc_options=[]):
		parser = OptionParser()
		parser.add_option('--lat_w', dest='lat_w', help='lattice width', type='int', action='store', default=128)
		parser.add_option('--lat_h', dest='lat_h', help='lattice height', type='int', action='store', default=128)
		parser.add_option('--visc', dest='visc', help='viscosity', type='float', action='store', default=0.01)
		parser.add_option('--scr_w', dest='scr_w', help='screen width', type='int', action='store', default=0)
		parser.add_option('--scr_h', dest='scr_h', help='screen height', type='int', action='store', default=0)
		parser.add_option('--scr_scale', dest='scr_scale', help='screen scale', type='float', action='store', default=3.0)
		parser.add_option('--every', dest='every', help='update the visualization every N steps', metavar='N', type='int', action='store', default=100)
		parser.add_option('--tracers', dest='tracers', help='number of tracer particles', type='int', action='store', default=32)
		parser.add_option('--model', dest='model', help='LBE model to use', type='choice', choices=['bgk', 'mrt'], action='store', default='bgk')
		parser.add_option('--vismode', dest='vismode', help='visualization mode', type='choice', choices=vis2d.vis_map.keys(), action='store', default='std')
		parser.add_option('--benchmark', dest='benchmark', help='benchmark mode, implies no visualization', action='store_true', default=False)
		parser.add_option('--max_iters', dest='max_iters', help='number of iterations to run in benchmark/batch mode', action='store', type='int', default=0)
		parser.add_option('--batch', dest='batch', help='run in batch mode, with no visualization', action='store_true', default=False)
		parser.add_option('--nobatch', dest='batch', help='run in interactive mode', action='store_false')
		parser.add_option('--accel_x', dest='accel_x', help='y component of the external acceleration', action='store', type='float', default=0.0)
		parser.add_option('--accel_y', dest='accel_y', help='x component of the external acceleration', action='store', type='float', default=0.0)
		parser.add_option('--periodic_x', dest='periodic_x', help='horizontally periodic lattice', action='store_true', default=False)
		parser.add_option('--periodic_y', dest='periodic_y', help='vertically periodic lattice', action='store_true', default=False)
		parser.add_option('--save_src', dest='save_src', help='file to save the CUDA source code to', action='store', type='string', default='')
		parser.add_option('--output', dest='output', help='save simulation results to FILE', metavar='FILE', action='store', type='string', default='')
		parser.add_option('--output_format', dest='output_format', help='output format', type='choice', choices=['h5nested', 'h5flat'], default='h5flat')
		parser.add_option('--precision', dest='precision', help='precision (single, double)', type='choice', choices=['single', 'double'], default='single')

		group = OptionGroup(parser, 'Simulation-specific options')
		for option in misc_options:
			group.add_option(option)

		parser.add_option_group(group)

		self.geo_class = geo_class
		self.options = Values(parser.defaults)
		parser.parse_args(sys.argv[1:], self.options)
		self.block_size = 64
		self._mlups_calls = 0
		self._mlups = 0.0
		self._iter_hooks = {}
		self._iter_hooks_every = {}

		if not self._is_double_precision():
			self.float = numpy.float32
		else:
			self.float = numpy.float64

	def _is_double_precision(self):
		return self.options.precision == 'double'

	def _calc_screen_size(self):
		# If the size of the window has not been explicitly defined, automatically adjust it
		# based on the size of the grid,
		if self.options.scr_w == 0:
			self.options.scr_w = self.options.lat_w * self.options.scr_scale

		if self.options.scr_h == 0:
			self.options.scr_h = self.options.lat_h * self.options.scr_scale

	def _init_vis(self):
		if not self.options.benchmark and not self.options.batch:
			self.vis = vis2d.Fluid2DVis(self.options.scr_w, self.options.scr_h,
										self.options.lat_w, self.options.lat_h)

	def add_iter_hook(self, i, func, every=False):
		if every:
			self._iter_hooks_every.setdefault(i, []).append(func)
		else:
			self._iter_hooks.setdefault(i, []).append(func)

	def get_tau(self):
		return self.float((6.0 * self.options.visc + 1.0)/2.0)

	def get_dist_size(self):
		return self.options.lat_w * self.options.lat_h

	def _init_code(self):
		# Particle distributions in host memory.
		self.dist = numpy.zeros((9, self.options.lat_h, self.options.lat_w), self.float)

		# Simulation geometry.
		self.geo = self.geo_class(self.options.lat_w, self.options.lat_h, self.options.model, self.options, self.float)
		self.geo.init_dist(self.dist)
		self.geo_params = self.float(self.geo.get_params())

		lbm_tmpl = Template(filename='lbm.mako')

		ctx = {}
		ctx['block_size'] = self.block_size
		ctx['lat_h'] = self.options.lat_h
		ctx['lat_w'] = self.options.lat_w
		ctx['num_params'] = len(self.geo_params)
		ctx['model'] = self.options.model
		ctx['periodic_x'] = int(self.options.periodic_x)
		ctx['periodic_y'] = int(self.options.periodic_y)
		ctx['dist_size'] = self.get_dist_size()
		ctx['ext_accel_x'] = '((float)%.20ff)' % self.options.accel_x
		ctx['ext_accel_y'] = '((float)%.20ff)' % self.options.accel_y
		ctx.update(self.geo.get_defines())

		src = lbm_tmpl.render(**ctx)

		if self._is_double_precision():
			src = _convert_to_double(src)

		if self.options.save_src:
			fsrc = open(self.options.save_src, 'w')
			print >>fsrc, src
			fsrc.close()

		self.mod = cuda.SourceModule(src, options=['--use_fast_math', '-Xptxas', '-v'])
		self.lbm_cnp = self.mod.get_function('LBMCollideAndPropagate')
		self.lbm_tracer = self.mod.get_function('LBMUpdateTracerParticles')

		# Set the 'tau' parameter.
		self.tau = self.get_tau()
		self.gpu_tau = self.mod.get_global('tau')[0]
		cuda.memcpy_htod(self.gpu_tau, self.tau)

		self.gpu_visc = self.mod.get_global('visc')[0]
		cuda.memcpy_htod(self.gpu_visc, self.float(self.options.visc))

		self.gpu_geo_params = self.mod.get_global('geo_params')[0]
		cuda.memcpy_htod(self.gpu_geo_params, self.geo_params)

	def _init_lbm(self):
		# Velocity and density.
		self.vx = numpy.zeros((self.options.lat_h, self.options.lat_w), self.float)
		self.vy = numpy.zeros((self.options.lat_h, self.options.lat_w), self.float)
		self.rho = numpy.zeros((self.options.lat_h, self.options.lat_w), self.float)
		self.gpu_vx = cuda.mem_alloc(self.vx.size * self.vx.dtype.itemsize)
		self.gpu_vy = cuda.mem_alloc(self.vy.size * self.vy.dtype.itemsize)
		self.gpu_rho = cuda.mem_alloc(self.rho.size * self.rho.dtype.itemsize)
		cuda.memcpy_htod(self.gpu_vx, self.vx)
		cuda.memcpy_htod(self.gpu_vy, self.vy)
		cuda.memcpy_htod(self.gpu_rho, self.rho)
		cuda.memcpy_htod(self.gpu_rho, self.rho)
		cuda.memcpy_htod(self.gpu_rho, self.rho)

		# Tracer particles.
		self.tracer_x = numpy.random.random_sample(self.options.tracers).astype(self.float) * self.options.lat_w
		self.tracer_y = numpy.random.random_sample(self.options.tracers).astype(self.float) * self.options.lat_h
		self.gpu_tracer_x = cuda.mem_alloc(self.tracer_x.size * self.tracer_x.dtype.itemsize)
		self.gpu_tracer_y = cuda.mem_alloc(self.tracer_y.size * self.tracer_y.dtype.itemsize)
		cuda.memcpy_htod(self.gpu_tracer_x, self.tracer_x)
		cuda.memcpy_htod(self.gpu_tracer_y, self.tracer_y)

		# Particle distributions in device memory, A-B access pattern.
		self.gpu_dist1 = cuda.mem_alloc(self.get_dist_size()*9*self.vx.dtype.itemsize)
		self.gpu_dist2 = cuda.mem_alloc(self.get_dist_size()*9*self.vx.dtype.itemsize)

		cuda.memcpy_htod(self.gpu_dist1, self.dist.flatten())
		cuda.memcpy_htod(self.gpu_dist2, self.dist.flatten())

		# Prepared calls to the kernel.
		self.lbm_cnp.prepare('P' * 6, block=(self.block_size,1,1), shared=(self.block_size*6*numpy.dtype(self.float()).itemsize))
		self.lbm_tracer.prepare('P' * 4, block=(self.options.tracers,1,1))

		# Kernel arguments.
		self.args_tracer2 = [self.gpu_dist1, self.geo.gpu_map, self.gpu_tracer_x, self.gpu_tracer_y]
		self.args_tracer1 = [self.gpu_dist2, self.geo.gpu_map, self.gpu_tracer_x, self.gpu_tracer_y]
		self.args1 = [self.geo.gpu_map, self.gpu_dist1, self.gpu_dist2, 0, 0, 0]
		self.args2 = [self.geo.gpu_map, self.gpu_dist2, self.gpu_dist1, 0, 0, 0]

		# Special argument list for the case where macroscopic quantities data is to be
		# saved in global memory, i.e. a visualization step.
		self.args1v = [self.geo.gpu_map, self.gpu_dist1, self.gpu_dist2, self.gpu_rho, self.gpu_vx, self.gpu_vy]
		self.args2v = [self.geo.gpu_map, self.gpu_dist2, self.gpu_dist1, self.gpu_rho, self.gpu_vx, self.gpu_vy]

		# Map: iteration parity -> kernel arguments to use.
		self.args_map = {
			0: (self.args1, self.args1v, self.args_tracer1),
			1: (self.args2, self.args2v, self.args_tracer2),
		}

	def sim_step(self, i, tracers=True, get_data=False):
		kargs = self.args_map[i & 1]

		if (not self.options.benchmark and i % self.options.every == 0) or get_data:
			self.lbm_cnp.prepared_call((self.options.lat_w/self.block_size, self.options.lat_h), *kargs[1])
			if tracers:
				self.lbm_tracer.prepared_call((1,1), *kargs[2])
				cuda.memcpy_dtoh(self.tracer_x, self.gpu_tracer_x)
				cuda.memcpy_dtoh(self.tracer_y, self.gpu_tracer_y)

			cuda.memcpy_dtoh(self.vx, self.gpu_vx)
			cuda.memcpy_dtoh(self.vy, self.gpu_vy)
			cuda.memcpy_dtoh(self.rho, self.gpu_rho)

			if self.options.output:
				self._output_data(i)
		else:
			self.lbm_cnp.prepared_call((self.options.lat_w/self.block_size, self.options.lat_h), *kargs[0])
			if tracers:
				self.lbm_tracer.prepared_call((1,1), *kargs[2])

	def get_mlups(self, tdiff, iters=None):
		if iters is not None:
			it = iters
		else:
			it = self.options.every

		mlups = float(it) * self.options.lat_w * self.options.lat_h * 1e-6 / tdiff
		self._mlups = (mlups + self._mlups * self._mlups_calls) / (self._mlups_calls + 1)
		self._mlups_calls += 1
		return (self._mlups, mlups)

	def _output_data(self, i):
		if self.options.output_format == 'h5flat':
			h5t = self.h5file.createGroup(self.h5grp, 'iter%d' % i, 'iteration %d' % i)
			self.h5file.createArray(h5t, 'v', numpy.dstack([self.vx, self.vy]), 'velocity')
			self.h5file.createArray(h5t, 'rho', self.rho, 'density')
		else:
			record = self.h5tbl.row
			record['iter'] = i
			record['vx'] = self.vx
			record['vy'] = self.vy
			record['rho'] = self.rho
			record.append()
			self.h5tbl.flush()

	def _init_output(self):
		if self.options.output:
			self.h5file = tables.openFile(self.options.output, mode='w')
			self.h5grp = self.h5file.createGroup('/', 'results', 'simulation results')
			self.h5file.setNodeAttr(self.h5grp, 'viscosity', self.options.visc)
			self.h5file.setNodeAttr(self.h5grp, 'accel_x', self.options.accel_x)
			self.h5file.setNodeAttr(self.h5grp, 'accel_y', self.options.accel_y)
			self.h5file.setNodeAttr(self.h5grp, 'sample_rate', self.options.every)
			self.h5file.setNodeAttr(self.h5grp, 'model', self.options.model)

			if self.options.output_format == 'h5nested':
				desc = {
					'iter': tables.Float32Col(pos=0),
					'vx': tables.Float32Col(pos=1, shape=self.vx.shape),
					'vy': tables.Float32Col(pos=2, shape=self.vy.shape),
					'rho': tables.Float32Col(pos=3, shape=self.rho.shape)
				}
				self.h5tbl = self.h5file.createTable(self.h5grp, 'results', desc, 'results')

	def _run_benchmark(self):
		self.iter = 0

		if self.options.max_iters:
			cycles = self.options.max_iters
		else:
			cycles = 1000
			print '# iters mlups_avg mlups_curr'

		import time

		while True:
			t_prev = time.time()

			for iter in range(0, cycles):
				self.sim_step(self.iter, tracers=False)
				self.iter += 1

			cuda.Context.synchronize()
			t_now = time.time()
			print self.iter,
			print '%.2f %.2f' % self.get_mlups(t_now - t_prev, cycles)

			if self.options.max_iters:
				break

	def _run_batch(self):
		assert self.options.max_iters > 0

		for self.iter in range(0, self.options.max_iters):
			need_data = False

			if self.iter in self._iter_hooks:
				need_data = True

			if not need_data:
				for k in self._iter_hooks_every:
					if self.iter % k == 0:
						need_data = True
						break

			self.sim_step(self.iter, tracers=False, get_data=need_data)

			if need_data:
				for hook in self._iter_hooks.get(self.iter, []):
					hook()
				for k, v in self._iter_hooks_every.iteritems():
					if self.iter % k == 0:
						for hook in v:
							hook()


	def run(self):
		self._calc_screen_size()
		self._init_vis()
		self._init_code()
		self._init_lbm()
		self._init_output()

		if self.options.benchmark:
			self._run_benchmark()
		elif self.options.batch:
			self._run_batch()
		else:
			self.vis.main(self)


