"""
FrankaPy Franka Panda Robot Control Library
"""
from setuptools import setup

requirements = [
    'autolab_core',
    'empy',
    'numpy',
    'numpy-quaternion',
    'numba',
    'catkin-pkg',
    'lark',
    'protobuf>=3.19.6'
]

setup(name='frankapy',
      version='2.0.0',
      description='FrankaPy Franka Panda Robot Control Library',
      author='Kevin Zhang, Mohit Sharma, Jacky Liang, Oliver Kroemer',
      author_email='',
      package_dir = {'': '.'},
      packages=['frankapy'],
      install_requires = requirements,
      extras_require = {}
     )
