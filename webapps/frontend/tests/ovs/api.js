// Copyright (C) 2016 iNuron NV
//
// This file is part of Open vStorage Open Source Edition (OSE),
// as available from
//
//      http://www.openvstorage.org and
//      http://www.openvstorage.com.
//
// This file is free software; you can redistribute it and/or modify it
// under the terms of the GNU Affero General Public License v3 (GNU AGPLv3)
// as published by the Free Software Foundation, in version 3 as it comes
// in the LICENSE.txt file of the Open vStorage OSE distribution.
//
// Open vStorage is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY of any kind.
/*global define, describe, beforeEach, spyOn, it, waitsFor, waits, runs, expect */
define(['ovs/api', 'ovs/shared', 'ovs/generic', 'jquery'], function(api, shared, generic, $) {
    'use strict';
    describe('API', function() {
        beforeEach(function() {
            spyOn($, 'ajax').andCallFake(function(url, data) {
                return $.Deferred(function(deferred) {
                    deferred.resolve({
                        url : url,
                        data: data
                    });
                }).promise();
            });
            shared.authentication = {
                header: function() {
                    return 'abc';
                },
                validate: function() {
                    return true;
                }
            };
        });

        it('every call should have a timestamp attached', function() {
            var returnValue, finished = false;

            runs(function() {
                api.get('api/dummy', {})
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.url).toContain('timestamp=' + generic.getTimestamp().toString().substr(0, 10));
            });
        });

        it('get should use http GET', function() {
            var returnValue, finished = false;

            runs(function() {
                api.get('api/dummy', {})
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.data.type).toBe('GET');
            });
        });

        it('post should use http POST', function() {
            var returnValue, finished = false;

            runs(function() {
                api.post('api/dummy', {})
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.data.type).toBe('POST');
            });
        });

        it('del should use http DELETE', function() {
            var returnValue, finished = false;

            runs(function() {
                api.del('api/dummy', {})
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.data.type).toBe('DELETE');
            });
        });

        it('calldata should be correct', function() {
            var expectedData = {
                    type: 'GET',
                    timeout: 1000 * 60 * 60,
                    contentType: 'application/json',
                    data: JSON.stringify({ abc: 123, def: 456 }),
                    headers: {
                        'Authorization': 'abc',
                        'X-CSRFToken'  : 'def'
                    }
                },
                returnValue, finished = false;

            runs(function() {
                generic.setCookie('csrftoken', 'def', { seconds: 1 });
                api.get('api/dummy', { data: { abc: 123, def: 456 } })
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.data).toEqual(expectedData);
            });
        });

        it('filter values should be in the querystring', function() {
            var returnValue, finished = false;

            runs(function() {
                api.get('api/dummy', { queryparams: { abc: 123, def: 456 } })
                    .done(function(value) {
                        returnValue = value;
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.url).toContain('abc=123&def=456');
            });
        });

        it ('the filter should fall back', function() {
            var finished = false;
            runs(function() {
                api.get('api/dummy', {})
                    .always(function() {
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(finished).toBe(true);
            });
        });

        it ('iterating the filter should only iterate the properties', function() {
            Object.prototype.invalidValue = 0;
            var finished = false;

            runs(function() {
                api.get('api/dummy', {})
                    .always(function() {
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(finished).toBe(true);
            });
        });
    });

    describe('API2', function() {
        beforeEach(function() {
            spyOn($, 'ajax').andCallFake(function() {
                return $.Deferred(function(deferred) {
                    deferred.reject({ readyState: 1, status: 1 }, 'textStatus', 'errorThrown');
                }).promise();
            });
            shared.authentication = {
                validate: function() {
                    return false;
                }
            };
        });

        it('a failed call should reject the promise', function() {
            var returnValue, finished = false;

            runs(function() {
                api.get('api/dummy', {})
                    .fail(function(xmlHttpRequest, textStatus, errorThrown) {
                        returnValue = {
                            xmlHttpRequest: xmlHttpRequest,
                            textStatus    : textStatus,
                            errorThrown   : errorThrown
                        };
                        finished = true;
                    });
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(returnValue.xmlHttpRequest).toEqual({ readyState: 1, status: 1 });
                expect(returnValue.textStatus).toBe('textStatus');
                expect(returnValue.errorThrown).toBe('errorThrown');
            });
        });
    });

    describe('API3', function() {
        beforeEach(function() {
            spyOn($, 'ajax').andCallFake(function() {
                return $.Deferred(function(deferred) {
                    deferred.reject({ readyState: 0, status: 1 }, 'textStatus', 'errorThrown');
                }).promise();
            });
        });

        it('navigating away should not call reject or resolve', function() {
            var deferred, promise, finished = false;
            runs(function() {
                deferred = $.Deferred(function(dfd) {
                    api.get('api/dummy', {}).always(dfd.resolve);
                });
                deferred.always(function() { finished = true; });
                promise = deferred.promise();
            });
            waits(10);
            runs(function() {
                expect(finished).toBe(false);
                deferred.resolve();
            });
            waitsFor(function() { return finished; }, 'The call should complete', 250);
            runs(function() {
                expect(finished).toBe(true);
            });
        });
    });
});
