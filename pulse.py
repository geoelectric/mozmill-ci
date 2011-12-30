import json
import optparse
import os
import sys

import jenkins
from mozillapulse import consumers


# Configuration settings which have to be moved out of the script
JENKINS_URL = 'http://localhost:8080'
JENKINS_USER = 'mozilla'
JENKINS_PASS = 'test1234'

PRODUCTS  = ['firefox', 'thunderbird']
BRANCHES  = ['mozilla-central', 'mozilla-aurora']
LOCALES   = ['de', 'en-US', 'fr', 'it', 'ja', 'es-ES', 'pl', 'pt-BR', 'ru', 'tr']
PLATFORMS = ['macosx', 'macosx64']


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


# Globally shared variables
debug = False
log_folder = None


def handle_notification(data, message):
    routing_key = data['_meta']['routing_key']

    # Create dictionary with properties of the build
    if data.get('payload') and data['payload'].get('build'):
        props = dict((k, v) for (k, v, source) in data['payload']['build'].get('properties'))
    else:
        props = dict()

    # Retrieve imporant properties
    product = props.get('product')
    branch = props.get('branch')
    buildid = props.get('buildid')
    locale = props.get('locale', 'en-US')
    platform = props.get('platform')
    version = props.get('appVersion')

    # If it's not a notificaton for a finished build process we are not interested in
    if not routing_key.endswith(".finished") or "test" in routing_key:
        return

    # If the product doesn't match the expected one we are not interested
    if not product in PRODUCTS:
        return

    # Output debug information if requested
    if debug:
        print "Routing Key: %s - Branch: %s" % (routing_key, branch)

        try:
            folder = os.path.join(log_folder, branch)
            if not os.path.exists(folder):
                os.makedirs(folder)
            f = open(os.path.join(folder, routing_key), 'w')
            f.write(json.dumps(data))
        finally:
            f.close()

    # Only the builders for l10n repacks offer the build id of the previous build
    # which we want to use for our update tests
    if not 'l10n' in routing_key:
        return

    # If the branch is not allowed we are not interested
    if not branch in BRANCHES:
        return

    # If the platform is not allowed we are not interested
    if not platform in PLATFORMS:
        return

    # If the locale is not allowed we are not interested
    if not locale in LOCALES:
        return

    # Only regular daily builds will provide a previous build id
    #if not props.get('previous_buildid'):
    #    return

    # Test for installer
    url = props.get('packageUrl')
    if props.has_key('installerFilename'):
        url = '/'.join([os.path.dirname(url), props.get('installerFilename')])

    print "Trigger update test: %(PRODUCT)s %(VERSION)s %(PLATFORM)s %(LOCALE)s %(BUILDID)s %(PREV_BUILDID)s" % {
              'PRODUCT': product,
              'VERSION': version,
              'PLATFORM': PLATFORM_MAP[platform],
              'LOCALE': locale,
              'BUILDID': buildid,
              'PREV_BUILDID': props.get('previous_buildid'),
              }

    # Trigger an update test
    j = jenkins.Jenkins(JENKINS_URL, JENKINS_USER, JENKINS_PASS)
    j.build_job('update-test', {'BRANCH': branch,
                                'PLATFORM': PLATFORM_MAP[platform],
                                'LOCALE': locale,
                                'BUILD_ID': props.get('previous_buildid'),
                                'TARGET_BUILD_ID': buildid })
    


def main():
    global debug
    global log_folder

    parser = optparse.OptionParser()
    parser.add_option("--debug",
                      dest='debug',
                      action="store_true",
                      default=False)
    parser.add_option("--log-folder",
                      dest="log_folder",
                      default="log",
                      help="Folder to write notification log files into")
    options, args = parser.parse_args()

    debug = options.debug
    log_folder = options.log_folder

    # Initialize Pulse connection
    pulse = consumers.BuildConsumer(applabel='qa-auto@mozilla.com|daily_testrun')
    pulse.configure(topic='#', callback=handle_notification)

    while True:
        try:
            pulse.listen()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception, e:
            print str(e)


if __name__ == "__main__":
    main()
