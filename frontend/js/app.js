var app = angular.module('jiraApp', ['ngRoute', 'ngSanitize']);

app.filter('formatUptime', function() {
    return function(seconds) {
        if (!seconds || seconds < 0) return '0s';
        var d = Math.floor(seconds / 86400);
        var h = Math.floor((seconds % 86400) / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = Math.floor(seconds % 60);
        var parts = [];
        if (d > 0) parts.push(d + 'd');
        if (h > 0) parts.push(h + 'h');
        if (m > 0) parts.push(m + 'm');
        if (parts.length === 0) parts.push(s + 's');
        return parts.join(' ');
    };
});

app.config(['$routeProvider', '$locationProvider', function($routeProvider, $locationProvider) {
    $routeProvider
        .when('/chat', {
            templateUrl: 'partials/chat.html',
            controller: 'ChatController'
        })
        .when('/triage', {
            templateUrl: 'partials/triage.html',
            controller: 'TriageController'
        })
        .when('/health', {
            templateUrl: 'partials/health.html',
            controller: 'HealthController'
        })
        .when('/metrics', {
            templateUrl: 'partials/metrics.html',
            controller: 'MetricsController'
        })
        .when('/tickets', {
            templateUrl: 'partials/tickets.html',
            controller: 'TicketsController'
        })
        .when('/feedback', {
            templateUrl: 'partials/feedback.html',
            controller: 'FeedbackController'
        })
        .otherwise({
            redirectTo: '/chat'
        });
}]);

app.run(['$rootScope', '$location', function($rootScope, $location) {
    $rootScope.isActive = function(path) {
        return $location.path() === path;
    };

    // Sidebar state
    $rootScope.sidebarCollapsed = false;
    $rootScope.pageTitle = 'AI Chat';

    $rootScope.toggleSidebar = function() {
        $rootScope.sidebarCollapsed = !$rootScope.sidebarCollapsed;
    };

    // Update page title based on route
    $rootScope.$on('$routeChangeSuccess', function(event, current) {
        var path = $location.path();
        var titles = {
            '/chat': 'AI Chat',
            '/triage': 'Triage & RCA',
            '/health': 'System Health',
            '/metrics': 'Observability Metrics',
            '/tickets': 'Triage Tickets',
            '/feedback': 'Feedback Analytics'
        };
        $rootScope.pageTitle = titles[path] || 'JIRA Agent';
    });

    // Modal state shared across all controllers
    $rootScope.showModal = false;
    $rootScope.modalTicket = null;
    $rootScope.modalLoading = false;
    $rootScope.modalError = null;

    $rootScope.openTicket = function(ticketId) {
        $rootScope.modalLoading = true;
        $rootScope.modalError = null;
        $rootScope.modalTicket = null;
        $rootScope.showModal = true;

        fetch('/api/ticket/' + encodeURIComponent(ticketId))
            .then(function(resp) {
                if (!resp.ok) throw new Error('Ticket not found');
                return resp.json();
            })
            .then(function(data) {
                $rootScope.$applyAsync(function() {
                    $rootScope.modalTicket = data;
                    $rootScope.modalLoading = false;
                });
            })
            .catch(function(err) {
                $rootScope.$applyAsync(function() {
                    $rootScope.modalError = err.message || 'Failed to load ticket';
                    $rootScope.modalLoading = false;
                });
            });
    };

    $rootScope.closeModal = function() {
        $rootScope.showModal = false;
        $rootScope.modalTicket = null;
        $rootScope.modalError = null;
    };
}]);

app.directive('ticketModal', function() {
    return {
        restrict: 'E',
        template: `
            <div class="modal-overlay" ng-if="$root.showModal" ng-click="$root.closeModal()">
                <div class="modal-content" ng-click="$event.stopPropagation()">
                    <div class="modal-header">
                        <h3 ng-if="$root.modalTicket">Ticket {{$root.modalTicket.ticket_id}}</h3>
                        <button class="modal-close" ng-click="$root.closeModal()">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div ng-if="$root.modalLoading" class="modal-loading">
                            <div class="spinner"></div>
                            <p>Loading ticket details...</p>
                        </div>
                        <div ng-if="$root.modalError" class="modal-error">{{$root.modalError}}</div>
                        <div ng-if="$root.modalTicket" class="ticket-detail">
                            <div class="ticket-detail-grid">
                                <div class="detail-section">
                                    <h4>Summary</h4>
                                    <p class="ticket-summary-text">{{$root.modalTicket.summary}}</p>
                                </div>
                                <div class="detail-row">
                                    <div class="detail-field">
                                        <label>Status</label>
                                        <span class="badge" ng-class="{'badge-success': $root.modalTicket.status==='Closed', 'badge-warning': $root.modalTicket.status==='Resolved', 'badge-info': $root.modalTicket.status==='Open'}">{{$root.modalTicket.status}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Priority</label>
                                        <span class="badge" ng-class="{'badge-danger': $root.modalTicket.priority==='P1', 'badge-warning': $root.modalTicket.priority==='P2', 'badge-info': $root.modalTicket.priority==='P3', 'badge-secondary': $root.modalTicket.priority==='P4'}">{{$root.modalTicket.priority}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Issue Type</label>
                                        <span>{{$root.modalTicket.issue_type}}</span>
                                    </div>
                                </div>
                                <div class="detail-row">
                                    <div class="detail-field">
                                        <label>Component</label>
                                        <span>{{$root.modalTicket.component}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Technology</label>
                                        <span>{{$root.modalTicket.technology}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Environment</label>
                                        <span>{{$root.modalTicket.environment}}</span>
                                    </div>
                                </div>
                                <div class="detail-row">
                                    <div class="detail-field">
                                        <label>Region</label>
                                        <span>{{$root.modalTicket.region}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Service</label>
                                        <span>{{$root.modalTicket.service}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Dependency</label>
                                        <span>{{$root.modalTicket.dependency}}</span>
                                    </div>
                                </div>
                                <div class="detail-section">
                                    <h4>Issue Description</h4>
                                    <p>{{$root.modalTicket.issue_description}}</p>
                                </div>
                                <div class="detail-section">
                                    <h4>Root Cause</h4>
                                    <p>{{$root.modalTicket.root_cause}}</p>
                                    <span class="rca-badge" ng-if="$root.modalTicket.root_cause_category">{{$root.modalTicket.root_cause_category}}</span>
                                </div>
                                <div class="detail-section">
                                    <h4>Solution</h4>
                                    <p>{{$root.modalTicket.solution}}</p>
                                </div>
                                <div class="detail-row">
                                    <div class="detail-field">
                                        <label>Resolution Time</label>
                                        <span>{{$root.modalTicket.estimated_resolution_time}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Key Metric</label>
                                        <span>{{$root.modalTicket.key_metric}}</span>
                                    </div>
                                </div>
                                <div class="detail-section" ng-if="$root.modalTicket.business_impact">
                                    <h4>Business Impact</h4>
                                    <p>{{$root.modalTicket.business_impact}}</p>
                                </div>
                                <div class="detail-section" ng-if="$root.modalTicket.tags">
                                    <h4>Tags</h4>
                                    <div class="tag-list">
                                        <span class="tag" ng-repeat="tag in $root.modalTicket.tags.split(',')">{{tag.trim()}}</span>
                                    </div>
                                </div>
                                <div class="detail-row">
                                    <div class="detail-field">
                                        <label>Created</label>
                                        <span>{{$root.modalTicket.created_date}}</span>
                                    </div>
                                    <div class="detail-field">
                                        <label>Resolved</label>
                                        <span>{{$root.modalTicket.resolved_date}}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `
    };
});
