// --solver-dvs smoke test: randomize() with the bundled dv-solve.
class Packet;
  rand bit [7:0] x;
  rand bit [4:0] y;
  constraint c {
    x > 10;
    x < 20;
    y != x[4:0];
  }
endclass

module smoke_dvs;
  initial begin
    static Packet p = new;
    for (int i = 0; i < 4; i++) begin
      if (p.randomize() != 1) $stop;
      if (p.x <= 10 || p.x >= 20 || p.y == p.x[4:0]) $stop;
    end
    $display("DVS OK");
    $finish;
  end
endmodule
