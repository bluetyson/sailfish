.. Sailfish documentation master file, created by
   sphinx-quickstart on Tue Nov  3 22:53:06 2009.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

#########################
Sailfish Reference Manual
#########################

:Release: |version|
:Date: |today|

.. module:: sailfish

Sailfish is a free computational fluid dynamics solver employing the Lattice Boltzmann
method and optimized for modern commodity high-performance computational systems,
especially Graphics Processing Units.

To illustrate how easy it is to create simulations using the Sailfish package,
here is a simple example code to simulate fluid flow in a lid-driven cavity::

    import lbm
    import geo

    class LBMGeoLDC(geo.LBMGeo2D):
        max_v = 0.1

        def _define_nodes(self):
            for i in range(0, self.lat_w):
                self.set_geo((i, 0), self.NODE_WALL)
                self.set_geo((i, self.lat_h-1), self.NODE_VELOCITY, (self.max_v, 0.0))
            for i in range(0, self.lat_h):
                self.set_geo((0, i), self.NODE_WALL)
                self.set_geo((self.lat_w-1, i), self.NODE_WALL)

        def init_dist(self, dist):
            self.velocity_to_dist((0,0), (0.0, 0.0), dist)
            self.fill_dist((0,0), dist)

            for i in range(0, self.lat_w):
                self.velocity_to_dist((i, self.lat_h-1), (self.max_v, 0.0), dist)

    class LDCSim(lbm.LBMSim):
        def __init__(self, geo_class):
            lbm.LBMSim.__init__(self, geo_class)

    sim = LDCSim(LBMGeoLDC)
    sim.run()


Contents
========

.. toctree::
   :maxdepth: 2

   main

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
