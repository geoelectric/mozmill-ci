#!/usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import copy
import json
import logging
import optparse
import os
import re
import socket
import sys

import jenkins
from mozillapulse import consumers


class NotFoundException(Exception):

    """Exception for a resource not being found (e.g. no logs)"""
    def __init__(self, message, location):
        self.location = location
        Exception.__init__(self, ': '.join([message, location]))


class JSONFile:

    def __init__(self, filename):
        self.filename = os.path.abspath(filename)


    def read(self):
        if not os.path.isfile(self.filename):
            raise NotFoundException('Specified file cannot be found.', self.filename)

        try:
            f = open(self.filename, 'r')
            return json.loads(f.read())
        finally:
            f.close()


    def write(self, data):
        try:
            folder = os.path.dirname(self.filename)
            if not os.path.exists(folder):
                os.makedirs(folder)
            f = open(self.filename, 'w')
            f.write(json.dumps(data))
        finally:
            f.close()


class Automation:

    def __init__(self, config, debug, log_folder, logger, message=None):
        self.config = config
        self.debug = debug
        self.log_folder = log_folder
        self.logger = logger

        self.jenkins = jenkins.Jenkins(self.config['jenkins']['url'],
                                       self.config['jenkins']['username'],
                                       self.config['jenkins']['password'])

        # Make the consumer dependent to the host to prevent queue corruption by
        # other machines we are using the same queue name
        applabel = '%s|%s' % (self.config['pulse']['applabel'], socket.getfqdn())

        # Initialize Pulse consumer with a non-durable view because we do not want
        # to queue up notifications if the consumer is not connected.
        pulse = consumers.BuildConsumer(applabel=applabel, durable=False)
        pulse.configure(callback=self.on_build,
                        topic=['build.*.*.finished', 'heartbeat'])
        self.logger.info('Connected to Mozilla Pulse as "%s".', applabel)

        if message:
            data = JSONFile(message).read()
            self.on_build(data, None)
        else:
            self.logger.info('Waiting for messages...')
            pulse.listen()


    def generate_job_parameters(self, testrun, node, platform, build_properties):
        # Create parameter map from Pulse to Jenkins properties
        map = self.config['testrun']['jenkins_parameter_map']
        parameter_map = copy.deepcopy(map['default']);
        if testrun in map:
            for key in map[testrun]:
                parameter_map[key] = map[testrun][key]

        # Create parameters and fill in values as given by the map
        parameters = { }
        for entry in parameter_map:
            value = None

            if 'key' in parameter_map[entry]:
                # A key means we have to retrieve a value from a dict
                value = build_properties.get(parameter_map[entry]['key'],
                                             parameter_map[entry].get('default'));
            elif 'value' in parameter_map[entry]:
                # A value means we have an hard-coded value
                value = parameter_map[entry]['value']

            if 'transform' in parameter_map[entry]:
                # A transformation method has to be called
                method = parameter_map[entry]['transform']
                value = Automation.__dict__[method](self, value)

            parameters[entry] = value

        # Add node and mozmill environment information
        parameters['NODES'] = node
        parameters['ENV_PLATFORM'] = self.get_mozmill_environment_platform(platform)

        return parameters


    def get_mozmill_environment_platform(self, platform):
        # Map to translate the platform to the mozmill environment platform

        ENVIRONMENT_PLATFORM_MAP = {
            'linux': 'linux',
            'linux64': 'linux',
            'mac': 'mac',
            'win32': 'windows',
            'win64': 'windows'
        }
        return ENVIRONMENT_PLATFORM_MAP[platform];


    def get_platform_identifier(self, platform):
        # Map to translate platform ids from Pulse to Mozmill / Firefox
        PLATFORM_MAP = {'linux': 'linux',
                        'linux-debug': 'linux',
                        'linux64': 'linux64',
                        'linux64-debug': 'linux64',
                        'macosx': 'mac',
                        'macosx-debug': 'mac',
                        'macosx64': 'mac',
                        'macosx64-debug': 'mac',
                        'win32': 'win32',
                        'win32-debug': 'win32',
                        'win64': 'win64',
                        'win64-debug': 'win64'}
        return PLATFORM_MAP[platform];


    def preprocess_message(self, data, message):
        # Ensure that the message gets removed from the queue
        if message is not None:
            message.ack()

        # Create dictionary with properties of the build
        if data.get('payload') and data['payload'].get('build'):
            build_properties= data['payload']['build'].get('properties')
            properties = dict((k, v) for (k, v, source) in build_properties)
        else:
            properties = dict()

        return (data['_meta']['routing_key'], properties)


    def on_build(self, data, message):
        (routing_key, props) = self.preprocess_message(data, message)

        # Check if the routing key matches the expected regex
        pattern = re.compile(self.config['pulse']['routing_key_regex'], re.IGNORECASE)
        if not pattern.match(routing_key):
            return

        # Cache often used properties
        branch = props.get('branch')
        locale = props.get('locale', 'en-US')
        platform = props.get('platform')
        product = props.get('product')

        # Output logging information for received notification
        self.logger.info("%s - Product: %s, Branch: %s, Platform: %s, Locale: %s" %
                         (data['_meta']['sent'], product, branch, platform, locale))

        # Save off the notification message if requested
        if self.debug:
            basename = '%(PRODUCT)s_%(PLATFORM)s_%(LOCALE)s_%(TIMESTAMP)s.log' % {
                           'PRODUCT': product,
                           'PLATFORM': platform,
                           'LOCALE': locale,
                           'TIMESTAMP': data['_meta']['sent']
                       }
            filename = os.path.join(self.log_folder, branch, basename)
            JSONFile(filename).write(data)

        # If one of the expected values do not match we are not interested in the build
        valid_branch = not self.config['pulse']['branches'] or branch in self.config['pulse']['branches']
        valid_locale = not self.config['pulse']['locales'] or locale in self.config['pulse']['locales']
        valid_platform = not self.config['pulse']['platforms'] or platform in self.config['pulse']['platforms']
        valid_product = not self.config['pulse']['products'] or product in self.config['pulse']['products']

        if not (valid_product and valid_branch and valid_platform and valid_locale):
            return

        # For Windows builds the packageURL references the zip file but we want the installer
        url = props.get('packageUrl')
        if props.has_key('installerFilename'):
            url = '/'.join([os.path.dirname(url), props.get('installerFilename')])

        self.logger.info("Trigger tests for %(PRODUCT)s %(VERSION)s %(PLATFORM)s %(LOCALE)s %(BUILDID)s %(PREV_BUILDID)s" % {
                  'PRODUCT': product,
                  'VERSION': props.get('appVersion'),
                  'PLATFORM': self.get_platform_identifier(platform),
                  'LOCALE': locale,
                  'BUILDID': props.get('buildid'),
                  'PREV_BUILDID': props.get('previous_buildid')
                  })

        # Queue up testruns for the branch as given by config settings
        target_branch = self.config['testrun']['by_branch'][branch]
        target_platform = self.get_platform_identifier(platform)

        for testrun in target_branch['testruns']:
            # Fire off a build for each supported platform
            for node in target_branch['platforms'][target_platform]:
                parameters = self.generate_job_parameters(testrun, node,
                                                          target_platform, props)
                self.jenkins.build_job('%s_%s' % (branch, testrun), parameters)


class DailyAutomation(Automation):

    def __init__(self, *args, **kwargs):
        Automation.__init__(self, *args, **kwargs)


def main():
    parser = optparse.OptionParser()
    parser.add_option('--debug',
                      dest='debug',
                      action='store_true',
                      default=False)
    parser.add_option('--log-folder',
                      dest='log_folder',
                      default='log',
                      help='Folder to write notification log files into')
    parser.add_option('--push-message',
                      dest='message',
                      help='Log file of a Pulse message to process for Jenkins')
    options, args = parser.parse_args()

    if not len(args):
        parser.error('A configuration file has to be passed in as first argument.')

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('automation')

    DailyAutomation(config=JSONFile(args[0]).read(),
                    debug=options.debug,
                    log_folder=options.log_folder,
                    logger=logger,
                    message=options.message)


if __name__ == "__main__":
    main()
