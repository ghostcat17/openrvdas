#!/usr/bin/env python3
"""API implementation for interacting with an in-memory data store,
along with a command-line script that exercises it.

See server/server_api.py for full documentation on the ServerAPI.
"""

import argparse
import logging
import os
import sys
import time

from json import dumps as json_dumps

sys.path.append('.')

from logger.utils.read_json import parse_json
from server.server_api import ServerAPI

DEFAULT_MAX_TRIES = 3
ID_SEPARATOR = ':'

################################################################################
class InMemoryServerAPI(ServerAPI):
  ############################
  def __init__(self):
    super().__init__()
    self.cruise_configs = {}
    self.mode = {}
    self.logger_config = {}
    self.callbacks = {}
    self.status = []

  #############################
  # API methods below are used in querying/modifying the API for the
  # record of the running state of loggers.
  ############################
  def get_cruises(self):
    """Return list of cruise id's."""
    return list(self.cruise_configs)

  ############################
  def get_cruise_config(self, cruise_id):
    """Return cruise config for specified cruise id."""
    cruise_config = self.cruise_configs.get(cruise_id, None)
    if not cruise_config:
      raise ValueError('No such cruise found: "%s"' % cruise_id)
    return cruise_config
  
  ############################
  def get_mode(self, cruise_id):
    """Return cruise config for specified cruise id."""
    return self.mode.get(cruise_id, None)

  ############################
  def get_modes(self, cruise_id):
    """Return list of modes defined for given cruise."""
    cruise_config = self.get_cruise_config(cruise_id)
    return list(cruise_config.get('modes', None))

  ############################
  def get_default_mode(self, cruise_id):
    """Get the name of the default mode for the specified cruise
    from the data store."""
    cruise_config = self.get_cruise_config(cruise_id)
    return cruise_config.get('default_mode', None)

  ############################
  def get_loggers(self, cruise_id):
    """Get a dict of {logger_id:logger_spec,...} defined for the
    specified cruise id in the data store. If cruise_id=None, get
    all loggers."""
    cruise_config = self.get_cruise_config(cruise_id)
    loggers = cruise_config.get('loggers', None)
    if not loggers:
      raise ValueError('No loggers found in cruise "%s"' % cruise_id)
    return loggers

  ############################
  def get_logger(self, cruise_id, logger):
    """Retrieve the logger spec for the specified logger id."""
    loggers = self.get_loggers(cruise_id)
    if not logger in loggers:
      raise ValueError('No logger "%s" found in cruise "%s"' %
                       (logger, cruise_id))
    return loggers.get(logger_id)

  ############################
  def get_configs(self, cruise_id=None, mode=None):
    """Retrieve the configs associated with a cruise id and mode from the
    data store. If mode is omitted, retrieve configs associated with
    the cruise's current logger configs. If cruise_id is omitted,
    return configs for *all* cruises."""
    if cruise_id:
      # If mode is specified, get the configs associated with that mode
      if mode:
        cruise_config = self.get_cruise_config(cruise_id)
        if not cruise_config:
          raise ValueError('Cruise "%s" not found in data store' % cruise_id)
        modes = cruise_config.get('modes', None)
        if not modes:
          raise ValueError('Cruise "%s" has no modes??' % cruise_id)
        if not mode in modes:
          raise ValueError('Cruise "%s" has no mode "%s"' % (cruise_id, mode))
        return modes[mode]

      # If mode is not specified, return current mode for each logger
      return {logger:self.get_config(cruise_id, logger)
              for logger in self.get_loggers(cruise_id)}

    # If cruise was omitted, return configs for *all* cruises. We
    # don't require that logger names be unique across cruises, so
    # munge logger name by prefixing cruise_id to keep them distinct.
    configs = {}
    for cruise_id in self.get_cruises():
      cruise_configs = self.get_configs(cruise_id, mode)
      # Munged logger name is 'cruise_id:logger'
      munged_configs = {(cruise_id + ID_SEPARATOR + logger):config
                        for logger, config in cruise_configs.items()}
      configs.update(munged_configs)
    return configs

  ############################
  def get_config(self, cruise_id, logger_id, mode=None):
    """Retrieve the config associated with the specified logger
    in the specified mode. If mode is omitted, retrieve logger's
    current config."""

    # First, get map of configs
    cruise_config = self.get_cruise_config(cruise_id)
    if not cruise_config:
      raise ValueError('No cruise config found for "%s"?!?' % cruise_id)
      
    configs = cruise_config.get('configs', None)
    if not configs:
      raise ValueError('Cruise "%s" has no configs?!?' % cruise_id)

    # If mode is not specified, get logger's current config name, then
    # look up matching config.
    if mode is None:
      cruise_configs = self.logger_config.get(cruise_id)
      if not cruise_configs:
        raise ValueError('No config defined for cruise "%s"?!?' % cruise_id)

      config_name = cruise_configs.get(logger_id, None)
      return configs.get(config_name, None)
    
    # If mode is specified, look up the config associated with this
    # logger in that mode
    cruise_modes = cruise_config.get('modes', None)
    if not cruise_modes:
      raise ValueError('Cruise "%s" has no modes?!?' % cruise_id)

    logger_config_dict = cruise_modes.get(mode, None)
    if logger_config_dict is None:
      raise ValueError('Cruise "%s" has no mode "%s"?!?' % (cruise_id, mode))

    config_name = logger_config_dict.get(logger, None)
    if not config_name:
      return None

    config = configs.get(config_name, None)
    if not config:
      raise ValueError('Cruise "%s" config "%s" not found?!?' %
                       (cruise_id, config_name))
    return config

  ############################
  # Methods for manipulating the desired state via API to indicate
  # current mode and which loggers should be in which configs.
  ############################
  def set_mode(self, cruise_id, mode):
    """Set the current mode of the specified cruise in the data store."""
    cruise_config = self.get_cruise_config(cruise_id)
    if not cruise_config:
      raise ValueError('Cruise "%s" not found in data store' % cruise_id)
    modes = cruise_config.get('modes', None)
    if not modes:
      raise ValueError('Cruise "%s" has no modes??' % cruise_id)
    if not mode in modes:
      raise ValueError('Cruise "%s" has no mode "%s"' % (cruise_id, mode))

    # Set new current mode in data store
    self.mode[cruise_id] = mode

    # Update the stored {logger:config_name} dict to match new mode
    # Here's a quick one-liner that doesn't do any checking:
    self.logger_config[cruise_id] = modes[mode].copy()

    # Here's s slow carefully-checked way of setting configs:
    #for logger, config in modes[mode].items():
    #  self.set_logger_config(cruise_id, logger, config)

    # Q: At this point should we could signal an update. Or we could
    # count on the API calling signal_update(). Or count on the update
    # being picked up by polling. For now, don't signal the update.

    #logging.warning('Signaling update')
    #self.signal_update(cruise_id)

  ############################
  def set_logger_config(self, cruise_id, logger, config):
    """Set specified logger to new config."""

    # First, follow down the data structure to make sure it's a valid
    # config for this logger.
    cruise_config = self.get_cruise_config(cruise_id)
    if not cruise_config:
      raise ValueError('Cruise "%s" not found in data store' % cruise_id)
    loggers = cruise_config.get('loggers', None)
    if not loggers:
      raise ValueError('Cruise "%s" has no loggers??' % cruise_id)
    logger_spec = loggers.get(logger, None)
    if not logger_spec:
      raise ValueError('Cruise "%s" has no logger "%s"??' % (cruise_id, logger))
    configs = logger_spec.get('configs', None)
    if not configs:
      raise ValueError('Logger "%s" in cruise "%s" has no configs??' %
                       (logger, cruise_id))
    if not config in configs:
      raise ValueError('Config "%s" not valid for logger "%s" in cruise "%s"' %
                       (config, logger, cruise_id))

    # If all is good, assign it
    if not cruise_id in self.logger_config:
      self.logger_config[cruise_id] = {}
    self.logger_config[cruise_id][logger] = config

    # Q: At this point should we notify someone (the LoggerServer?)
    # that config has changed? A: For now, we're relying on someone
    # calling signal_update() once they've made the changes they want.

  #############################
  # API method to register a callback. When the data store changes,
  # methods that are registered via on_update() will be called so they
  # can fetch updated results. If cruise_id==None, make callback when
  # any cruise_id update is registered.
  #############################
  def on_update(self, callback, kwargs=None, cruise_id=None):
    """Register a method to be called when datastore changes."""
    if not cruise_id in self.callbacks:
      self.callbacks[cruise_id] = []
    if kwargs is None:
      kwargs = {}
    self.callbacks[cruise_id].append((callback, kwargs))

  #############################
  def signal_update(self, cruise_id=None):
    """Call the registered methods when an update has been signalled."""
    if cruise_id in self.callbacks:
      for (callback, kwargs) in self.callbacks[cruise_id]:
        logging.debug('Executing update callback for cruise %s: %s',
                      cruise_id, callback)
      callback(**kwargs)

    # If cruise_id is *not* None, then we've now done the callbacks
    # for that specified cruise. But we may also have callbacks (filed
    # under None) that are supposed to be executed when *any* cruise
    # is updated. Do those now.
    if cruise_id is not None:
      self.signal_update(cruise_id=None)

  ############################
  # Methods for feeding data from LoggerServer back into the API
  ############################
  def update_status(self, status):
    """Save/register the loggers' retrieved status report with the API."""
    logging.info('Got status: %s', status)
    self.status.append( (time.time(), status) )

  #############################
  # Methods to modify the data store
  ############################
  def load_cruise(self, cruise_config):
    """Add a complete cruise configuration (id, modes, configs, 
    default) to the data store."""
    if 'cruise' in cruise_config and 'id' in cruise_config['cruise']:
      cruise_id = cruise_config['cruise']['id']
    else:
      cruise_id = 'cruise_%d' % len(cruise_configs.keys())

    if ID_SEPARATOR in cruise_id:
      raise ValueError('Illegal character "%s" in cruise id: "%s"' %
                       ID_SEPARATOR, cruise_id)

    self.cruise_configs[cruise_id] = cruise_config

    # Set cruise into default mode, if one is defined
    if 'default_mode' in cruise_config:
      self.set_mode(cruise_id, cruise_config['default_mode'])

  ############################
  def delete_cruise(self, cruise_id):
    """Remove the specified cruise from the data store."""
    if cruise_id in self.cruise_configs:
      del self.cruise_configs[cruise_id]
    else:
      logging.error('Trying to delete undefined cruise "%s"', cruise_id)

    if cruise_id in self.mode:
      del self.mode[cruise_id]
    if cruise_id in self.logger_config:
      del self.logger_config[cruise_id]
    if cruise_id in self.callbacks:
      del self.callbacks[cruise_id]
      
  #def add_cruise(self, cruise_id, start=None, end=None)
  #def add_mode(self, cruise_id, mode)
  #def delete_mode(self, cruise_id, mode)
  #def add_logger(self, cruise_id, logger_id, logger_spec)
  #def delete_logger(self, cruise_id, logger_id)
  #def add_config(self, cruise_id, config, config_spec)
  #def add_config_to_logger(self, cruise_id, config, logger_id)
  #def add_config_to_mode(self, cruise_id, config, logger_id, mode)
  #def delete_config(self, cruise_id, config_id)


"""
In Django, we'd implement update_status as follows:

        # If there's anything notable - an error or change of state -
        # create a new LoggerConfigState to document it.
        for logger_id, logger_status in status.items():
          config = configs.get(logger_id, None)
          old_config = old_configs.get(logger_id, None)
          old_logger_status = old_status.get(logger_id, {})

          try:
            logger = Logger.objects.get(id=logger_id)
          except Logger.DoesNotExist:
            logging.warning('No logger corresponding to id %d?!?', logger_id)
            continue
          
          if (logger_status.get('errors', None) or
              logger_status != old_logger_status or
              config != old_config):
            running = bool(logger_status.get('running', False))
            errors = ', '.join(logger_status.get('errors', []))
            pid = logger_status.get('pid', None)
            logging.info('Updating %s config: %s; running: %s, errors: %s',
                         logger, config.name if config else '-none-',
                         running, errors or 'None')
            LoggerConfigState(logger=logger, config=config, running=running,
                              process_id=pid, errors=errors).save()
 
"""