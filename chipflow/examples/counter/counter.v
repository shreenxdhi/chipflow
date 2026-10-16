module counter(input clk, input reset, output reg [7:0] count);
  always @(posedge clk)
    if (reset) count <= 0;
    else count <= count + 1'b1;
endmodule
