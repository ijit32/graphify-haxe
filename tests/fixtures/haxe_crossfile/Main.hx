package com.graphify.app;

import com.graphify.app.Service;

class Main {
    public function new() {}
    public function run():Void {
        var s = new Service();
        s.serve();
    }
}
