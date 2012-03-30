""" Script to create and trigger Firefox testruns in Jenkins. """

import ConfigParser
import re
import sys

import jenkins


def get_mozmill_environment_platform(platform):
    # Map to translate the platform to the mozmill environment platform

    ENVIRONMENT_PLATFORM_MAP = {
        'linux': 'linux',
        'linux64': 'linux',
        'mac': 'mac',
        'win32': 'windows',
        'win64': 'windows'
    }
    return ENVIRONMENT_PLATFORM_MAP[platform]

def main():
    j = jenkins.Jenkins('http://localhost:8080')

    if not len(sys.argv) == 2:
        print 'Configuration file not specified.'
        print 'Usage: %s config' % sys.argv[0]
        sys.exit(1)

    # Read-in configuration options
    config = ConfigParser.SafeConfigParser()
    config.read(sys.argv[1])

    # Check testrun entries
    testrun = { }
    for entry in config.options('testrun'):
        testrun.update({entry: config.get('testrun', entry)})
    testrun = testrun

    script = testrun['script']
    report_url = getattr(testrun, 'report', 'http://mozmill-ondemand.blargon7.com/db/')

    # Iterate through all target nodes
    for section in config.sections():
        # Retrieve the platform, i.e. win32 or linux64
        if not config.has_option(section, 'platform'):
            continue
        platform = config.get(section, 'platform')
        node_labels = section.split(' ')

        # Iterate through all builds per platform
        for entry in config.options(section):
            locales = [ ]
            build_type = 'release'

            # Expression to parse versions like: '5.0', '5.0#3', '5.0b1', '5.0b2#1'
            pattern = re.compile(r'(?P<version>[\d\.]+(?:\w\d+)?)(?:#(?P<build>\d+))?')
            try:
                (version, build) = pattern.match(entry).group('version', 'build')
                locales = config.get(section, entry).split(' ')

                # If a build number has been specified we have a candidate build
                build_type = 'candidate' if build else build_type
            except:
                continue

            for locale in locales:
                parameters = {
                    'BUILD_TYPE': build_type,
                    'BUILD_NUMBER': build or '1',
                    'ENV_PLATFORM': get_mozmill_environment_platform(platform),
                    'LOCALE': locale,
                    'NODES': ' && '.join(node_labels),
                    'PLATFORM': platform,
                    'REPORT_URL': report_url
                }
                #print 'Triggering job: ondemand_%s with %s' % (script, parameters)
                self.jenkins.build_job('ondemand_%s' % testrun['script'], parameters)

if __name__ == "__main__":
    main()
