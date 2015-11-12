# Copyright (c) 2014 Hewlett-Packard Development Company, L.P.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import warnings

from keystoneauth1 import plugin
from keystoneauth1 import session
import requestsexceptions

from os_client_config import _log
from os_client_config import exceptions


class CloudConfig(object):
    def __init__(self, name, region, config,
                 force_ipv4=False, auth_plugin=None,
                 openstack_config=None):
        self.name = name
        self.region = region
        self.config = config
        self.log = _log.setup_logging(__name__)
        self._force_ipv4 = force_ipv4
        self._auth = auth_plugin
        self._openstack_config = openstack_config
        self._keystone_session = None

    def __getattr__(self, key):
        """Return arbitrary attributes."""

        if key.startswith('os_'):
            key = key[3:]

        if key in [attr.replace('-', '_') for attr in self.config]:
            return self.config[key]
        else:
            return None

    def __iter__(self):
        return self.config.__iter__()

    def __eq__(self, other):
        return (self.name == other.name and self.region == other.region
                and self.config == other.config)

    def __ne__(self, other):
        return not self == other

    def get_requests_verify_args(self):
        """Return the verify and cert values for the requests library."""
        if self.config['verify'] and self.config['cacert']:
            verify = self.config['cacert']
        else:
            verify = self.config['verify']
            if self.config['cacert']:
                warnings.warn(
                    "You are specifying a cacert for the cloud {0} but "
                    "also to ignore the host verification. The host SSL cert "
                    "will not be verified.".format(self.name))

        cert = self.config.get('cert', None)
        if cert:
            if self.config['key']:
                cert = (cert, self.config['key'])
        return (verify, cert)

    def get_services(self):
        """Return a list of service types we know something about."""
        services = []
        for key, val in self.config.items():
            if (key.endswith('api_version')
                    or key.endswith('service_type')
                    or key.endswith('service_name')):
                services.append("_".join(key.split('_')[:-2]))
        return list(set(services))

    def get_auth_args(self):
        return self.config['auth']

    def get_interface(self, service_type=None):
        interface = self.config.get('interface')
        if not service_type:
            return interface
        key = '{service_type}_interface'.format(service_type=service_type)
        return self.config.get(key, interface)

    def get_region_name(self, service_type=None):
        if not service_type:
            return self.region
        key = '{service_type}_region_name'.format(service_type=service_type)
        return self.config.get(key, self.region)

    def get_api_version(self, service_type):
        key = '{service_type}_api_version'.format(service_type=service_type)
        return self.config.get(key, None)

    def get_service_type(self, service_type):
        key = '{service_type}_service_type'.format(service_type=service_type)
        return self.config.get(key, service_type)

    def get_service_name(self, service_type):
        key = '{service_type}_service_name'.format(service_type=service_type)
        return self.config.get(key, None)

    def get_endpoint(self, service_type):
        key = '{service_type}_endpoint'.format(service_type=service_type)
        return self.config.get(key, None)

    @property
    def prefer_ipv6(self):
        return not self._force_ipv4

    @property
    def force_ipv4(self):
        return self._force_ipv4

    def get_auth(self):
        """Return a keystoneauth plugin from the auth credentials."""
        return self._auth

    def get_session(self):
        """Return a keystoneauth session based on the auth credentials."""
        if self._keystone_session is None:
            if not self._auth:
                raise exceptions.OpenStackConfigException(
                    "Problem with auth parameters")
            (verify, cert) = self.get_requests_verify_args()
            # Turn off urllib3 warnings about insecure certs if we have
            # explicitly configured requests to tell it we do not want
            # cert verification
            if not verify:
                self.log.debug(
                    "Turning off SSL warnings for {cloud}:{region}"
                    " since verify=False".format(
                        cloud=self.name, region=self.region))
            requestsexceptions.squelch_warnings(insecure_requests=not verify)
            self._keystone_session = session.Session(
                auth=self._auth,
                verify=verify,
                cert=cert,
                timeout=self.config['api_timeout'])
        return self._keystone_session

    def get_session_endpoint(self, service_key):
        """Return the endpoint from config or the catalog.

        If a configuration lists an explicit endpoint for a service,
        return that. Otherwise, fetch the service catalog from the
        keystone session and return the appropriate endpoint.

        :param service_key: Generic key for service, such as 'compute' or
                            'network'

        :returns: Endpoint for the service, or None if not found
        """

        override_endpoint = self.get_endpoint(service_key)
        if override_endpoint:
            return override_endpoint
        # keystone is a special case in keystone, because what?
        session = self.get_session()
        if service_key == 'identity':
            endpoint = session.get_endpoint(interface=plugin.AUTH_INTERFACE)
        else:
            endpoint = session.get_endpoint(
                service_type=self.get_service_type(service_key),
                service_name=self.get_service_name(service_key),
                interface=self.get_interface(service_key),
                region_name=self.region)
        return endpoint

    def get_legacy_client(
            self, service_key, client_class, interface_key=None,
            pass_version_arg=True, **kwargs):
        """Return a legacy OpenStack client object for the given config.

        Most of the OpenStack python-*client libraries have the same
        interface for their client constructors, but there are several
        parameters one wants to pass given a :class:`CloudConfig` object.

        In the future, OpenStack API consumption should be done through
        the OpenStack SDK, but that's not ready yet. This is for getting
        Client objects from python-*client only.

        :param service_key: Generic key for service, such as 'compute' or
                            'network'
        :param client_class: Class of the client to be instantiated. This
                             should be the unversioned version if there
                             is one, such as novaclient.client.Client, or
                             the versioned one, such as
                             neutronclient.v2_0.client.Client if there isn't
        :param interface_key: (optional) Some clients, such as glanceclient
                              only accept the parameter 'interface' instead
                              of 'endpoint_type' - this is a get-out-of-jail
                              parameter for those until they can be aligned.
                              os-client-config understands this to be the
                              case if service_key is image, so this is really
                              only for use with other unknown broken clients.
        :param pass_version_arg: (optional) If a versioned Client constructor
                                 was passed to client_class, set this to
                                 False, which will tell get_client to not
                                 pass a version parameter. os-client-config
                                 already understand that this is the
                                 case for network, so it can be omitted in
                                 that case.
        :param kwargs: (optional) keyword args are passed through to the
                       Client constructor, so this is in case anything
                       additional needs to be passed in.
        """
        # Because of course swift is different
        if service_key == 'object-store':
            return self._get_swift_client(client_class=client_class, **kwargs)
        interface = self.get_interface(service_key)
        # trigger exception on lack of service
        endpoint = self.get_session_endpoint(service_key)

        if not interface_key:
            if service_key == 'image':
                interface_key = 'interface'
            else:
                interface_key = 'endpoint_type'

        constructor_kwargs = dict(
            session=self.get_session(),
            service_name=self.get_service_name(service_key),
            service_type=self.get_service_type(service_key),
            region_name=self.region)

        if service_key == 'image':
            # os-client-config does not depend on glanceclient, but if
            # the user passed in glanceclient.client.Client, which they
            # would need to do if they were requesting 'image' - then
            # they necessarily have glanceclient installed
            from glanceclient.common import utils as glance_utils
            endpoint, version = glance_utils.strip_version(endpoint)
            constructor_kwargs['endpoint'] = endpoint
        constructor_kwargs.update(kwargs)
        constructor_kwargs[interface_key] = interface
        constructor_args = []
        if pass_version_arg:
            version = self.get_api_version(service_key)
            # Temporary workaround while we wait for python-openstackclient
            # to be able to handle 2.0 which is what neutronclient expects
            if service_key == 'network' and version == '2':
                version = '2.0'
            if service_key == 'identity':
                # Workaround for bug#1513839
                if 'endpoint' not in constructor_kwargs:
                    endpoint = self.get_session_endpoint('identity')
                    constructor_kwargs['endpoint'] = endpoint
            constructor_args.append(version)

        return client_class(*constructor_args, **constructor_kwargs)

    def _get_swift_client(self, client_class, **kwargs):
        session = self.get_session()
        token = session.get_token()
        endpoint = self.get_session_endpoint(service_key='object-store')
        if not endpoint:
            return None
        return client_class(
            preauthurl=endpoint,
            preauthtoken=token,
            auth_version=self.get_api_version('identity'),
            os_options=dict(
                auth_token=token,
                object_storage_url=endpoint,
                region_name=self.get_region_name()),
            timeout=self.api_timeout,
        )

    def get_cache_expiration_time(self):
        if self._openstack_config:
            return self._openstack_config.get_cache_expiration_time()

    def get_cache_path(self):
        if self._openstack_config:
            return self._openstack_config.get_cache_path()

    def get_cache_class(self):
        if self._openstack_config:
            return self._openstack_config.get_cache_class()

    def get_cache_arguments(self):
        if self._openstack_config:
            return self._openstack_config.get_cache_arguments()

    def get_cache_expiration(self):
        if self._openstack_config:
            return self._openstack_config.get_cache_expiration()

    def get_cache_resource_expiration(self, resource, default=None):
        """Get expiration time for a resource

        :param resource: Name of the resource type
        :param default: Default value to return if not found (optional,
                        defaults to None)

        :returns: Expiration time for the resource type as float or default
        """
        if self._openstack_config:
            expiration = self._openstack_config.get_cache_expiration()
            if resource not in expiration:
                return default
            return float(expiration[resource])
