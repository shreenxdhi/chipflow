// Independent domains only. This example contains no CDC synchronizer or crossing.
module multiclock(input clk_a, input clk_b, input reset,
                  output reg [7:0] count_a, output reg [7:0] count_b);
  always @(posedge clk_a) begin
    if(reset) count_a <= 0; else count_a <= count_a + 1'b1;
  end
  always @(posedge clk_b) begin
    if(reset) count_b <= 0; else count_b <= count_b + 1'b1;
  end
endmodule
