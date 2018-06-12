// Copyright (C) 2018 iNuron NV
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
/*global define, window */
define(['jquery', 'knockout',
    'durandal/system', 'durandal/activator',
    'ovs/routing', 'ovs/shared', 'ovs/viewcache'
], function ($, ko,
             system, activator,
             routing, shared, viewcache) {
    "use strict";

    function PluginLoader() {
        var self = this;
        self.pages = {};
        self.wizards = {};
        self.dashboards = [];
    }

    var functions = {
        load_hooks: function (plugin) {
            var self = this;
            return $.when().then(function () {
                // Requirejs works with callbacks. To chain it, wrap it up
                return $.Deferred(function (moduleLoadingDeferred){
                    require(['ovs/hooks/' + plugin], function (hook) { // webapps/frontend/lib/ovs/hooks
                        var systemLoaders = [];
                        routing.extraRoutes.push(hook.routes);
                        routing.routePatches.push(hook.routePatches);
                        $.each(hook.dashboards, function (index, dashboard) {
                            systemLoaders.push(system.acquire('viewmodels/site/' + dashboard)
                                .then(function (module) {
                                    var moduleInstance = new module();
                                    var view = {
                                        module: moduleInstance,
                                        module_constructor: module,
                                        activator: activator.create(),
                                        type: 'dashboard'
                                    };
                                    self.dashboards.push(view);
                                }));
                        });
                        $.each(hook.wizards, function (wizard, moduleName) {
                            if (!shared.hooks.wizards.hasOwnProperty(wizard)) {
                                shared.hooks.wizards[wizard] = [];
                            }
                            systemLoaders.push(system.acquire('viewmodels/wizards/' + wizard + '/' + moduleName)
                                .then(function (module) {
                                    var moduleInstance = new module();
                                    var view = {
                                        name: moduleName,
                                        module: moduleInstance,
                                        module_constructor: module,
                                        activator: activator.create(),
                                        type: 'wizard'
                                    };
                                    shared.hooks.wizards[wizard].push(view);
                                }));
                        });
                        $.each(hook.pages, function (page, pageInfo) {
                            if (!self.pages.hasOwnProperty(page)) {
                                self.pages[page] = [];
                            }
                            $.each(pageInfo, function (index, info) {
                                var moduleName;
                                if (typeof info === 'string') {
                                    moduleName = info;
                                    info = {type: 'generic', module: moduleName};
                                } else {
                                    moduleName = info.module;
                                }
                                systemLoaders.push(system.acquire('viewmodels/site/' + moduleName)
                                    .then(function (module) {
                                        // Always returns the same instance when item would get activated!
                                        var moduleInstance = new module();
                                        var view = {
                                            info: info,
                                            name: moduleName,
                                            module: moduleInstance,
                                            activator: activator.create(),
                                            module_constructor: module,
                                            type: 'page',
                                            page: page,
                                            plugin: plugin
                                        };
                                        self.pages[page].push(plugin, view);  // Used in shell js
                                        viewcache.put(plugin, view)
                                    }));
                            });
                        });
                        return $.when.apply(self, systemLoaders)
                            // Even when failing to load. Resolve routing
                            .always(moduleLoadingDeferred.resolve)
                });
                }).promise();
            }).then(function(){
                // All loaded up!
                return self;
            })
        },
        get_plugin_pages: function (page, identifier) {
            var self = this;
            var out = [];
            var cachedPages = viewcache.get_by_page(page, identifier);
            if (cachedPages.length > 0){
                return cachedPages
            }
            // Need new module instances
            var standardPages = viewcache.get_by_page(page); // Loaded on boot time
            return standardPages.reduce(function(acc, cur) {
                if (cur.type !== 'page') {
                    return acc // Continue
                }
                var module_constructor = cur['module_constructor'];
                var module_instance = new module_constructor();
                var new_page = $.extend({}, cur, {'module': module_instance, 'activator': activator.create()});
                viewcache.put(new_page['plugin'], new_page, identifier);
                acc.push(new_page);
                return acc;
            }, []);
        },
        activate_page: function (page) {
            page.activator.activateItem(page.module).fail(function (error) {
                console.error(error)
            })
        },
        deactivate_page: function (page) {
            page.activator.deactivateItem(page.module);
        }
    };

    PluginLoader.prototype = $.extend({}, functions);
    return new PluginLoader()
});