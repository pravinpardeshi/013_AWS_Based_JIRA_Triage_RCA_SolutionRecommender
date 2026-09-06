app.factory('ChatState', function() {
    return {
        messages: [],
        inputMessage: '',
        loading: false,
        streaming: false,
        topK: 5,
        sessionId: null,
        sessionTitle: null
    };
});

app.factory('TriageState', function() {
    return {
        description: '',
        component: '',
        technology: '',
        environment: '',
        loading: false,
        streaming: false,
        result: null
    };
});

app.factory('ApiService', ['$http', function($http) {
    var baseUrl = '/api';

    function _parseSSEEvents(reader, decoder, onEvent) {
        var eventType = null;
        var dataLines = [];

        function readChunk() {
            return reader.read().then(function(result) {
                if (result.done) {
                    if (eventType && dataLines.length > 0) {
                        try {
                            var data = JSON.parse(dataLines.join('\n'));
                            onEvent(eventType, data);
                        } catch(e) {}
                    }
                    return;
                }
                var text = decoder.decode(result.value, { stream: true });
                var lines = text.split('\n');

                for (var i = 0; i < lines.length; i++) {
                    var line = lines[i];
                    if (line.indexOf('event: ') === 0) {
                        eventType = line.substring(7).trim();
                    } else if (line.indexOf('data: ') === 0) {
                        dataLines.push(line.substring(6));
                    } else if (line === '' && eventType && dataLines.length > 0) {
                        try {
                            var data = JSON.parse(dataLines.join('\n'));
                            onEvent(eventType, data);
                        } catch(e) {}
                        eventType = null;
                        dataLines = [];
                    }
                }
                return readChunk();
            });
        }

        return readChunk();
    }

    function streamPost(url, body, callbacks) {
        var abortController = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var opts = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        };
        if (abortController) opts.signal = abortController.signal;

        fetch(url, opts).then(function(response) {
            if (!response.ok) {
                return response.json().then(function(err) {
                    callbacks.onError(err.detail || 'Request failed');
                });
            }
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            return _parseSSEEvents(reader, decoder, function(eventType, data) {
                if (eventType === 'init') {
                    callbacks.onInit(data);
                } else if (eventType === 'token') {
                    callbacks.onToken(data.content);
                } else if (eventType === 'done') {
                    callbacks.onDone(data);
                }
            });
        }).catch(function(err) {
            if (err.name !== 'AbortError') {
                callbacks.onError(err.message || 'Stream failed');
            }
        });

        return {
            abort: function() {
                if (abortController) abortController.abort();
            }
        };
    }

    return {
        chat: function(message, topK) {
            return $http.post(baseUrl + '/chat', {
                message: message,
                top_k: topK || 5
            });
        },

        chatStream: function(message, topK, sessionId, callbacks) {
            return streamPost(baseUrl + '/chat/stream', {
                message: message,
                top_k: topK || 5,
                session_id: sessionId || null
            }, callbacks);
        },

        triage: function(data) {
            return $http.post(baseUrl + '/triage', {
                description: data.description,
                component: data.component || '',
                technology: data.technology || '',
                environment: data.environment || ''
            });
        },

        triageStream: function(data, callbacks) {
            return streamPost(baseUrl + '/triage/stream', {
                description: data.description,
                component: data.component || '',
                technology: data.technology || '',
                environment: data.environment || ''
            }, callbacks);
        },

        getTicket: function(ticketId) {
            return $http.get(baseUrl + '/ticket/' + encodeURIComponent(ticketId));
        },

        getStats: function() {
            return $http.get(baseUrl + '/stats');
        },

        // Session APIs
        listSessions: function() {
            return $http.get(baseUrl + '/sessions');
        },

        createSession: function(title) {
            return $http.post(baseUrl + '/sessions', { title: title || 'Untitled Chat' });
        },

        getSession: function(id) {
            return $http.get(baseUrl + '/sessions/' + id);
        },

        renameSession: function(id, title) {
            return $http.put(baseUrl + '/sessions/' + id, { title: title });
        },

        deleteSession: function(id) {
            return $http.delete(baseUrl + '/sessions/' + id);
        },

        saveMessage: function(sessionId, role, content, sources) {
            return $http.post(baseUrl + '/sessions/' + sessionId + '/messages?role=' +
                encodeURIComponent(role) + '&content=' + encodeURIComponent(content) +
                '&sources=' + encodeURIComponent(JSON.stringify(sources || [])));
        },

        // Triage ticket APIs
        createTriageTicket: function(data) {
            return $http.post(baseUrl + '/triage/tickets', {
                description: data.description,
                component: data.component || '',
                technology: data.technology || '',
                environment: data.environment || '',
                analysis: data.analysis,
                similar_tickets: data.similar_tickets || [],
                metrics: data.metrics || {}
            });
        },

        listTriageTickets: function(status, search) {
            var url = baseUrl + '/triage/tickets';
            var params = [];
            if (status && status !== 'all') params.push('status=' + encodeURIComponent(status));
            if (search) params.push('search=' + encodeURIComponent(search));
            if (params.length) url += '?' + params.join('&');
            return $http.get(url);
        },

        getTriageTicket: function(id) {
            return $http.get(baseUrl + '/triage/tickets/' + id);
        },

        updateTriageTicketStatus: function(id, status, resolutionNotes) {
            return $http.patch(baseUrl + '/triage/tickets/' + id + '/status', {
                status: status,
                resolution_notes: resolutionNotes || null
            });
        },

        updateTriageTicket: function(id, data) {
            return $http.patch(baseUrl + '/triage/tickets/' + id, {
                description: data.description,
                component: data.component,
                technology: data.technology,
                environment: data.environment
            });
        },

        saveTriageFeedback: function(id, rating, comment) {
            return $http.post(baseUrl + '/triage/tickets/' + id + '/feedback', {
                rating: rating,
                comment: comment || null
            });
        }
    };
}]);
