package com.graphify.test;

import haxe.ds.StringMap;
import haxe.ds.IntMap;
import Math;
import haxe.ds.StringMap as SM;

using StringTools;

@:keep
@:generic
class Main extends Base implements IFoo {
  static var count:Int = 0;
  public var name(get, set):String;

  @:pure
  public function new() {
    super();
    this.name = "graphify";
  }

  static function main() {
    trace("Hello, World!");
    var m = new Main();
    m.process();
    var arr = [1, 2, 3];
    var doubled = arr.map(function(x) return x * 2);
    var tripled = arr.map(x -> x * 3);
  }

  public function process():Void {
    trace("processing");
    helper();
  }

  private function helper():Void {
    trace("helping");
  }

  function get_name():String {
    return name;
  }

  function set_name(v:String):String {
    return this.name = v;
  }

  public function testCast():Void {
    var x:Dynamic = 42;
    var y = cast (x, ExternalBind);
    var z:ExternalBind = (x : ExternalBind);
  }

  public function testSwitch(c:Color):Void {
    switch (c) {
      case Red: trace("red");
      case Green: trace("green");
      case Blue: trace("blue");
      case Rgb(r, _, _): trace("rgb");
    }
  }

  public function testObject():Void {
    var o = { name: "test", value: 42 };
    trace(o.name);
  }

  public function testExpressionMeta(x:Dynamic):Void {
    var a = @:keep someCall();
    var t = $type(x);
  }
}

interface IFoo {
  public function doSomething():Void;
}

@:generic
enum Result<T> {
  Ok(v:T);
  Error(msg:String, code:Int);
}

enum Color {
  Red;
  Green;
  Blue;
  Rgb(r:Int, g:Int, b:Int);
}

typedef Point = {x:Float, y:Float};

abstract MyAbstract(Int) {
  public function new(v:Int) {
    this = v;
  }
  public function value():Int {
    return this;
  }
}

extern class ExternalBind {
  public function bind():Void;
}

#if debug
var debugMode:Bool = true;
#else
var releaseMode:Bool = true;
#end
