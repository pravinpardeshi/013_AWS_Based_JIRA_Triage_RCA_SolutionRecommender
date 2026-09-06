app.controller('MainController', ['$scope', '$rootScope', function($scope, $rootScope) {
    $scope.sidebarCollapsed = false;
    $scope.pageTitle = 'AI Chat';
    $scope.isDarkTheme = localStorage.getItem('theme') === 'dark';

    if ($scope.isDarkTheme) {
        document.documentElement.setAttribute('data-theme', 'dark');
    }

    $scope.toggleSidebar = function() {
        $scope.sidebarCollapsed = !$scope.sidebarCollapsed;
    };

    $scope.toggleTheme = function() {
        $scope.isDarkTheme = !$scope.isDarkTheme;
        if ($scope.isDarkTheme) {
            document.documentElement.setAttribute('data-theme', 'dark');
            localStorage.setItem('theme', 'dark');
        } else {
            document.documentElement.removeAttribute('data-theme');
            localStorage.setItem('theme', 'light');
        }
    };

    $rootScope.$on('$routeChangeSuccess', function(event, current) {
        var path = window.location.hash.replace('#!', '') || '/chat';
        var titles = {
            '/chat': 'AI Chat',
            '/triage': 'Triage & RCA',
            '/health': 'System Health',
            '/metrics': 'Observability Metrics',
            '/tickets': 'Triage Tickets',
            '/feedback': 'Feedback Analytics'
        };
        $scope.pageTitle = titles[path] || 'JIRA Agent';
    });
}]);

app.controller('ChatController', ['$scope', 'ApiService', 'ChatState', function($scope, ApiService, ChatState) {
    var state = ChatState;
    $scope.messages = state.messages;
    $scope.inputMessage = state.inputMessage;
    $scope.loading = state.loading;
    $scope.streaming = state.streaming;
    $scope.topK = state.topK;
    $scope.sessions = [];
    $scope.activeSessionId = state.sessionId;
    var activeStream = null;

    $scope.$watch('inputMessage', function(v) { state.inputMessage = v; });
    $scope.$watch('topK', function(v) { state.topK = v; });
    $scope.$watch('loading', function(v) { state.loading = v; });
    $scope.$watch('streaming', function(v) { state.streaming = v; });

    $scope.loadSessions = function() {
        ApiService.listSessions().then(function(res) {
            $scope.sessions = res.data;
        });
    };
    $scope.loadSessions();

    $scope.newChat = function() {
        if (activeStream) activeStream.abort();
        state.messages.length = 0;
        state.streaming = false;
        state.loading = false;
        state.sessionId = null;
        state.sessionTitle = null;
        $scope.activeSessionId = null;
    };

    $scope.loadSession = function(session) {
        ApiService.getSession(session.id).then(function(res) {
            if (activeStream) activeStream.abort();
            state.messages.length = 0;
            var msgs = res.data.messages || [];
            for (var i = 0; i < msgs.length; i++) {
                var m = msgs[i];
                state.messages.push({
                    role: m.role,
                    content: m.content,
                    sources: m.role === 'assistant' ? (typeof m.sources === 'string' ? JSON.parse(m.sources) : (m.sources || [])) : [],
                    metrics: null,
                    streaming: false
                });
            }
            state.sessionId = session.id;
            state.sessionTitle = session.title;
            $scope.activeSessionId = session.id;
            $scope.loading = false;
            $scope.streaming = false;
            scrollToBottom();
        });
    };

    $scope.deleteSession = function(session, ev) {
        ev.stopPropagation();
        if (!confirm('Delete "' + session.title + '"?')) return;
        ApiService.deleteSession(session.id).then(function() {
            if ($scope.activeSessionId === session.id) $scope.newChat();
            $scope.loadSessions();
        });
    };

    $scope.renameSession = function(session, ev) {
        ev.stopPropagation();
        var newTitle = prompt('Rename session:', session.title);
        if (newTitle && newTitle.trim()) {
            ApiService.renameSession(session.id, newTitle.trim()).then(function() {
                session.title = newTitle.trim();
                if ($scope.activeSessionId === session.id) state.sessionTitle = newTitle.trim();
            });
        }
    };

    $scope.sendMessage = function() {
        if (!$scope.inputMessage.trim() || $scope.loading) return;

        var userMsg = $scope.inputMessage.trim();
        $scope.inputMessage = '';
        $scope.loading = true;
        $scope.streaming = true;

        // Ensure we have a session before sending
        var sendWithSession = function(sessionId) {
            $scope.messages.push({ role: 'user', content: userMsg });

            var assistantMsg = {
                role: 'assistant',
                content: '',
                sources: [],
                metrics: null,
                streaming: true
            };
            $scope.messages.push(assistantMsg);
            scrollToBottom();

            activeStream = ApiService.chatStream(userMsg, $scope.topK, sessionId, {
                onInit: function(data) {
                    $scope.$applyAsync(function() {
                        assistantMsg.sources = data.sources || [];
                    });
                },
                onToken: function(token) {
                    $scope.$applyAsync(function() {
                        assistantMsg.content += token;
                        scrollToBottom();
                    });
                },
                onDone: function(metrics) {
                    $scope.$applyAsync(function() {
                        assistantMsg.metrics = metrics;
                        assistantMsg.streaming = false;
                        $scope.loading = false;
                        $scope.streaming = false;
                        activeStream = null;
                        scrollToBottom();
                    });
                },
                onError: function(err) {
                    $scope.$applyAsync(function() {
                        assistantMsg.content = assistantMsg.content || 'Error: ' + err;
                        assistantMsg.isError = !assistantMsg.content;
                        assistantMsg.streaming = false;
                        $scope.loading = false;
                        $scope.streaming = false;
                        activeStream = null;
                    });
                }
            });
        };

        if (!$scope.activeSessionId) {
            var title = userMsg.length > 50 ? userMsg.substring(0, 50) + '...' : userMsg;
            ApiService.createSession(title).then(function(res) {
                $scope.activeSessionId = res.data.id;
                state.sessionId = res.data.id;
                state.sessionTitle = res.data.title;
                $scope.loadSessions();
                sendWithSession(res.data.id);
            });
        } else {
            sendWithSession($scope.activeSessionId);
        }
    };

    $scope.stopStreaming = function() {
        if (activeStream) {
            activeStream.abort();
            activeStream = null;
            $scope.loading = false;
            $scope.streaming = false;
            var last = $scope.messages[$scope.messages.length - 1];
            if (last && last.streaming) {
                last.streaming = false;
            }
        }
    };

    $scope.clearChat = function() {
        if (activeStream) activeStream.abort();
        state.messages.length = 0;
        state.streaming = false;
        state.loading = false;
    };

    function scrollToBottom() {
        setTimeout(function() {
            var chatBox = document.getElementById('chatMessages');
            if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;
        }, 10);
    }

    $scope.formatMarkdown = function(text) {
        if (!text) return '';
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    };
}]);


app.controller('TriageController', ['$scope', 'ApiService', 'TriageState', function($scope, ApiService, TriageState) {
    $scope.state = TriageState;
    $scope.environments = ['Production', 'Staging', 'QA', 'Development', 'UAT'];
    $scope.priorities = ['P1 - Critical', 'P2 - High', 'P3 - Medium', 'P4 - Low'];
    var activeStream = null;

    $scope.submitTriage = function() {
        if (!$scope.state.description.trim() || $scope.state.loading) return;

        $scope.state.loading = true;
        $scope.state.streaming = true;
        $scope.state.result = null;

        activeStream = ApiService.triageStream({
            description: $scope.state.description,
            component: $scope.state.component,
            technology: $scope.state.technology,
            environment: $scope.state.environment
        }, {
            onInit: function(data) {
                $scope.$applyAsync(function() {
                    $scope.state.result = {
                        analysis: '',
                        similar_tickets: data.similar_tickets || [],
                        metrics: null,
                        streaming: true
                    };
                });
            },
            onToken: function(token) {
                $scope.$applyAsync(function() {
                    $scope.state.result.analysis += token;
                });
            },
            onDone: function(metrics) {
                $scope.$applyAsync(function() {
                    $scope.state.result.metrics = metrics;
                    $scope.state.result.streaming = false;
                    $scope.state.loading = false;
                    $scope.state.streaming = false;
                    activeStream = null;
                });
            },
            onError: function(err) {
                $scope.$applyAsync(function() {
                    $scope.state.result = {
                        analysis: 'Error: ' + err,
                        similar_tickets: [],
                        isError: true,
                        streaming: false
                    };
                    $scope.state.loading = false;
                    $scope.state.streaming = false;
                    activeStream = null;
                });
            }
        });
    };

    $scope.stopStreaming = function() {
        if (activeStream) {
            activeStream.abort();
            activeStream = null;
            $scope.state.loading = false;
            $scope.state.streaming = false;
            if ($scope.state.result) $scope.state.result.streaming = false;
        }
    };

    $scope.reset = function() {
        if (activeStream) activeStream.abort();
        TriageState.description = '';
        TriageState.component = '';
        TriageState.technology = '';
        TriageState.environment = '';
        TriageState.result = null;
        TriageState.loading = false;
        TriageState.streaming = false;
    };

    $scope.formatMarkdown = function(text) {
        if (!text) return '';
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    };

    $scope.saveAsTicket = function() {
        if (!$scope.state.result || $scope.state.savingTicket) return;
        $scope.state.savingTicket = true;
        ApiService.createTriageTicket({
            description: $scope.state.description,
            component: $scope.state.component,
            technology: $scope.state.technology,
            environment: $scope.state.environment,
            analysis: $scope.state.result.analysis,
            similar_tickets: $scope.state.result.similar_tickets || [],
            metrics: $scope.state.result.metrics || {}
        }).then(function(response) {
            $scope.state.savingTicket = false;
            $scope.state.ticketSaved = true;
        }).catch(function(err) {
            $scope.state.savingTicket = false;
            console.error('Failed to save ticket:', err);
        });
    };
}]);

app.controller('HealthController', ['$scope', '$interval', function($scope, $interval) {
    $scope.health = null;
    $scope.loading = true;
    $scope.error = null;
    $scope.autoRefresh = false;
    $scope.lastChecked = null;
    var refreshTimer = null;

    $scope.refresh = function() {
        $scope.loading = !$scope.health;
        $scope.error = null;
        fetch('/api/health')
            .then(function(resp) {
                if (!resp.ok) throw new Error('Health check failed');
                return resp.json();
            })
            .then(function(data) {
                $scope.$applyAsync(function() {
                    $scope.health = data;
                    $scope.loading = false;
                    $scope.lastChecked = new Date();
                });
            })
            .catch(function(err) {
                $scope.$applyAsync(function() {
                    $scope.error = err.message || 'Failed to reach server';
                    $scope.loading = false;
                });
            });
    };

    $scope.toggleAutoRefresh = function() {
        if ($scope.autoRefresh) {
            refreshTimer = $interval($scope.refresh, 5000);
        } else if (refreshTimer) {
            $interval.cancel(refreshTimer);
            refreshTimer = null;
        }
    };

    $scope.$on('$destroy', function() {
        if (refreshTimer) $interval.cancel(refreshTimer);
    });

    $scope.refresh();
}]);

app.controller('MetricsController', ['$scope', '$interval', function($scope, $interval) {
    $scope.data = null;
    $scope.loading = true;
    $scope.error = null;
    $scope.autoRefresh = false;
    $scope.lastRefreshed = null;
    var refreshTimer = null;

    $scope.refresh = function() {
        $scope.loading = !$scope.data;
        $scope.error = null;
        fetch('/api/metrics/summary')
            .then(function(resp) {
                if (!resp.ok) throw new Error('Failed to fetch metrics');
                return resp.json();
            })
            .then(function(data) {
                $scope.$applyAsync(function() {
                    $scope.data = data;
                    $scope.loading = false;
                    $scope.lastRefreshed = new Date();
                });
            })
            .catch(function(err) {
                $scope.$applyAsync(function() {
                    $scope.error = err.message || 'Failed to reach server';
                    $scope.loading = false;
                });
            });
    };

    $scope.toggleAutoRefresh = function() {
        if ($scope.autoRefresh) {
            refreshTimer = $interval($scope.refresh, 5000);
        } else if (refreshTimer) {
            $interval.cancel(refreshTimer);
            refreshTimer = null;
        }
    };

    $scope.$on('$destroy', function() {
        if (refreshTimer) $interval.cancel(refreshTimer);
    });

    $scope.parseLabel = function(key, field) {
        var parts = key.split('_');
        if (field === 'method') return parts[0] || '';
        if (field === 'status') return parts[parts.length - 1] || '';
        return key;
    };

    $scope.parseDbOp = function(key) {
        return key || '';
    };

    $scope.parseDbStatus = function(key) {
        var parts = key.split('_');
        return parts[parts.length - 1] || 'ok';
    };

    $scope.totalBlocked = function() {
        if (!$scope.data || !$scope.data.guardrails || !$scope.data.guardrails.blocked) return 0;
        var total = 0;
        var blocked = $scope.data.guardrails.blocked;
        for (var k in blocked) {
            if (blocked.hasOwnProperty(k)) total += blocked[k];
        }
        return total;
    };

    $scope.circuitStateLabel = function() {
        if (!$scope.data || !$scope.data.circuit_breaker) return '---';
        var s = $scope.data.circuit_breaker.state;
        if (s === 0) return 'CLOSED';
        if (s === 1) return 'OPEN';
        if (s === 2) return 'HALF-OPEN';
        return 'UNKNOWN';
    };

    $scope.refresh();
}]);


app.controller('TicketsController', ['$scope', 'ApiService', function($scope, ApiService) {
    $scope.tickets = [];
    $scope.selectedTicket = null;
    $scope.loading = true;
    $scope.error = null;
    $scope.searchQuery = '';
    $scope.statusFilter = 'all';
    $scope.feedback = { rating: 0, comment: '' };
    $scope.hoverRating = 0;
    $scope.editing = false;
    $scope.editData = {};
    $scope.environments = ['Production', 'Staging', 'QA', 'Development', 'UAT'];

    $scope.loadTickets = function() {
        $scope.loading = true;
        ApiService.listTriageTickets($scope.statusFilter, $scope.searchQuery)
            .then(function(response) {
                $scope.tickets = response.data;
                $scope.loading = false;
            })
            .catch(function(err) {
                $scope.error = 'Failed to load tickets: ' + (err.data && err.data.detail || err.statusText);
                $scope.loading = false;
            });
    };

    $scope.filterTickets = function() {
        $scope.loadTickets();
    };

    $scope.selectTicket = function(ticket) {
        ApiService.getTriageTicket(ticket.id)
            .then(function(response) {
                $scope.selectedTicket = response.data;
                $scope.feedback = { rating: 0, comment: '' };
                $scope.editing = false;
            })
            .catch(function(err) {
                $scope.error = 'Failed to load ticket: ' + (err.data && err.data.detail || err.statusText);
            });
    };

    $scope.deselectTicket = function() {
        $scope.selectedTicket = null;
        $scope.editing = false;
        $scope.loadTickets();
    };

    $scope.startEdit = function() {
        $scope.editData = {
            description: $scope.selectedTicket.description,
            component: $scope.selectedTicket.component,
            technology: $scope.selectedTicket.technology,
            environment: $scope.selectedTicket.environment
        };
        $scope.editing = true;
    };

    $scope.cancelEdit = function() {
        $scope.editing = false;
        $scope.editData = {};
    };

    $scope.saveEdit = function() {
        ApiService.updateTriageTicket($scope.selectedTicket.id, $scope.editData)
            .then(function(response) {
                $scope.selectedTicket = response.data;
                $scope.editing = false;
                $scope.editData = {};
            })
            .catch(function(err) {
                $scope.error = 'Failed to update ticket: ' + (err.data && err.data.detail || err.statusText);
            });
    };

    $scope.updateStatus = function() {
        ApiService.updateTriageTicketStatus($scope.selectedTicket.id, $scope.selectedTicket.status)
            .then(function(response) {
                $scope.selectedTicket = response.data;
            })
            .catch(function(err) {
                $scope.error = 'Failed to update status: ' + (err.data && err.data.detail || err.statusText);
            });
    };

    $scope.setRating = function(rating) {
        $scope.feedback.rating = rating;
    };

    $scope.getRating = function(rating) {
        return new Array(rating || 0);
    };

    $scope.getEmptyRating = function(rating) {
        return new Array(Math.max(0, 5 - (rating || 0)));
    };

    $scope.submitFeedback = function() {
        if (!$scope.feedback.rating) return;
        ApiService.saveTriageFeedback($scope.selectedTicket.id, $scope.feedback.rating, $scope.feedback.comment)
            .then(function(response) {
                $scope.selectedTicket = response.data;
                $scope.feedback = { rating: 0, comment: '' };
            })
            .catch(function(err) {
                $scope.error = 'Failed to submit feedback: ' + (err.data && err.data.detail || err.statusText);
            });
    };

    $scope.renderAnalysis = function(text) {
        if (!text) return '';
        return text
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.*?)\*/g, '<em>$1</em>')
            .replace(/`(.*?)`/g, '<code>$1</code>')
            .replace(/\n/g, '<br>');
    };

    $scope.loadTickets();
}]);


app.controller('FeedbackController', ['$scope', function($scope) {
    $scope.stats = null;
    $scope.lowRated = [];
    $scope.adjustments = { active: [], all: [] };
    $scope.loading = true;
    $scope.error = null;
    $scope.analyzing = false;
    $scope.analysisResult = null;

    $scope.refresh = function() {
        $scope.loading = true;
        $scope.error = null;

        Promise.all([
            fetch('/api/feedback/stats').then(function(r) { return r.json(); }),
            fetch('/api/feedback/low-rated').then(function(r) { return r.json(); }),
            fetch('/api/feedback/adjustments').then(function(r) { return r.json(); })
        ]).then(function(results) {
            $scope.$applyAsync(function() {
                $scope.stats = results[0];
                $scope.lowRated = results[1];
                $scope.adjustments = results[2];
                $scope.loading = false;
            });
        }).catch(function(err) {
            $scope.$applyAsync(function() {
                $scope.error = 'Failed to load feedback data: ' + (err.message || err);
                $scope.loading = false;
            });
        });
    };

    $scope.runAnalysis = function() {
        $scope.analyzing = true;
        $scope.analysisResult = null;

        fetch('/api/feedback/analyze', { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                $scope.$applyAsync(function() {
                    $scope.analysisResult = data;
                    $scope.analyzing = false;
                    $scope.refresh();
                });
            })
            .catch(function(err) {
                $scope.$applyAsync(function() {
                    $scope.analysisResult = { status: 'error', message: 'Analysis failed: ' + err.message };
                    $scope.analyzing = false;
                });
            });
    };

    $scope.toggleAdjustment = function(adj) {
        if (adj.active) {
            fetch('/api/feedback/adjustments/' + adj.adjustment_id + '/deactivate', { method: 'POST' })
                .then(function() {
                    $scope.$applyAsync(function() { $scope.refresh(); });
                });
        }
    };

    $scope.getRating = function(rating) {
        return new Array(rating || 0);
    };

    $scope.getBarWidth = function(count) {
        if (!$scope.stats || !$scope.stats.total_feedback || $scope.stats.total_feedback === 0) return '0%';
        return Math.round((count / $scope.stats.total_feedback) * 100) + '%';
    };

    $scope.refresh();
}]);
